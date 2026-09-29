import base64
import json
import sys
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, images, quadlet, stack, workloads  # noqa: E402
from fake_host import FakeHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class WorkloadsTests(unittest.TestCase):
    def test_each_workload_is_idempotent_and_preserves_storage(self):
        for function, names, obsolete in (
            (workloads.install_postgres, ['todo-postgres'],
             ['todo-postgres-data', 'todo-postgres-backup']),
            (workloads.install_application, ['todo-app'], []),
            (partial(workloads.install_postgres, database=apps.APPS[1].database), ['notes-postgres'],
             ['notes-postgres-data', 'notes-postgres-backup']),
            (partial(workloads.install_application, app=apps.APPS[1]), ['notes-app'], []),
            (workloads.install_keycloak, ['keycloak'], []),
            (workloads.install_shared_proxy, ['shared-proxy'], ['todo-nginx-data']),
        ):
            with self.subTest(function=str(function)), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                directory = base / 'quadlet'
                directory.mkdir()
                runtime = directory / 'todo-kube-runtime'
                rendered = base / 'rendered'
                rendered.mkdir()
                for name in ('postgres', 'keycloak', 'app', 'shared-proxy', 'config',
                             'notes-app', 'notes-postgres', 'notes-config'):
                    (rendered / f'{name}.yaml').write_text(f'fixture: {name}\n')
                for name in obsolete + ['unrelated']:
                    (directory / f'{name}.volume').touch()
                with FakeHost(password=' password-with-spaces \n') as host:
                    self.assertTrue(function(ROOT, directory, runtime, rendered))
                    initial = {p: p.stat().st_mtime_ns for p in runtime.iterdir()}
                    first_run = list(host.calls)
                    host.calls.clear()
                    self.assertFalse(function(ROOT, directory, runtime, rendered))
                    self.assertEqual(initial, {p: p.stat().st_mtime_ns for p in runtime.iterdir()})
                    self.assertFalse(host.ran('podman', 'secret', 'create'))
                    config = ('keycloak.yaml' if function == workloads.install_keycloak else
                              'notes-config.yaml' if names[0].startswith('notes-') else 'config.yaml')
                    (rendered / config).write_text('changed: true\n')
                    self.assertTrue(function(ROOT, directory, runtime, rendered))
                # Each Kube secret carries the raw password, spaces and all, under its own name;
                # apart from secrets, a workload only reloads systemd.
                for argv in (a for a in first_run if a[:3] == ['podman', 'secret', 'create']):
                    payload = json.loads(host.secrets[argv[3]])
                    self.assertEqual(payload['metadata']['name'], argv[3])
                    for value in payload['data'].values():
                        self.assertEqual(base64.b64decode(value), b' password-with-spaces ')
                self.assertEqual({tuple(a) for a in first_run + host.calls if a[:2] != ['podman', 'secret']}
                                 - {('podman', 'kube', 'play', '--help')},
                                 {('systemctl', '--user', 'daemon-reload')})
                for name in names:
                    self.assertTrue((runtime / f'{name}.kube').is_file())
                self.assertEqual(list(directory.glob('*.volume')), [directory / 'unrelated.volume'])
                self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
                manifest = ('keycloak.yaml' if function == workloads.install_keycloak else
                            'notes-config.yaml' if names[0].startswith('notes-') else 'config.yaml')
                self.assertEqual((runtime / manifest).stat().st_mode & 0o777, 0o600)

    def test_invalid_runtime_directory_fails_before_commands(self):
        with patch('subprocess.run') as run:
            with self.assertRaises(ValueError):
                workloads.install_postgres(ROOT, '/tmp/q', '/tmp/elsewhere', '/tmp/rendered')
            run.assert_not_called()

    def test_shared_proxy_refuses_a_wildcard_publish_address(self):
        # deploy/quadlet/shared-proxy.kube.j2 always keeps a fixed 127.0.0.1
        # binding alongside the requested one; a wildcard address would try
        # to bind the same port twice and podman would refuse to start.
        for wildcard in ('0.0.0.0', '::'):
            with patch('subprocess.run') as run:
                with self.assertRaisesRegex(ValueError, 'wildcard address'):
                    workloads.install_shared_proxy(ROOT, '/tmp/q', '/tmp/q/todo-kube-runtime',
                                                   '/tmp/rendered', publish_address=wildcard)
                run.assert_not_called()
        with patch('subprocess.run') as run:
            with self.assertRaises(ValueError):
                workloads.install_shared_proxy(ROOT, '/tmp/q', '/tmp/q/todo-kube-runtime',
                                               '/tmp/rendered', publish_address='not-an-address')
            run.assert_not_called()

    def test_default_postgres_address_is_optional_in_plain_jinja(self):
        rendered = quadlet.render(ROOT, 'todo-postgres.kube', {}).decode()
        self.assertEqual(rendered.count('PublishPort='), 1)
        self.assertIn('PublishPort=127.0.0.1:5432:5432\n', rendered)

    def test_databases_publish_distinct_ports_from_the_registry(self):
        for app in apps.APPS:
            rendered = quadlet.render(ROOT, app.database.unit, {
                'postgres_publish_port': app.replication_port,
                'postgres_publish_address': '192.0.2.50',
            }).decode()
            self.assertIn(f'PublishPort=127.0.0.1:{app.replication_port}:5432\n', rendered)
            self.assertIn(f'PublishPort=192.0.2.50:{app.replication_port}:5432\n', rendered)
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'verified DR group'):
                workloads.install_postgres(ROOT, '/q', '/q/todo-kube-runtime', '/rendered',
                                           publish_address='192.0.2.1',
                                           database=stack.Database(name='third'))
            run.assert_not_called()

    def test_image_build_load_and_identity(self):
        for mode, present, refresh in [('build', False, False), ('build', True, False),
                                       ('build', True, True), ('offline', False, False)]:
            with self.subTest(mode=mode, present=present, refresh=refresh), \
                    FakeHost(images_present=present) as host:
                calls = host.calls
                changed = images.prepare(ROOT, mode, '/bundle', refresh)
                self.assertEqual(set(changed.values()), {not present or refresh})
                if mode == 'offline':
                    self.assertEqual(sum(a[1] == 'load' for a in calls), 5)
                elif not present or refresh:
                    self.assertEqual(sum(a[1] == 'build' for a in calls), 4)
                    self.assertIn(['podman', 'pull', 'docker.io/library/postgres:17.11'], calls)
