import base64
import json
import re
import sys
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import (  # noqa: E402
    apps,
    bundle,
    images,
    platform_file,
    quadlet,
    stack,
    workloads,
)
from fake_host import FakeHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class WorkloadsTests(unittest.TestCase):
    def test_each_workload_is_idempotent_and_preserves_storage(self):
        for function, names, obsolete in (
            (partial(workloads.install_postgres, database=platform_file.checkout().apps[0].database), ['todo-postgres'],
             ['todo-postgres-data', 'todo-postgres-backup']),
            (partial(workloads.install_application, app=platform_file.checkout().apps[0]), ['todo-app'], []),
            (partial(workloads.install_postgres, database=platform_file.checkout().apps[1].database), ['notes-postgres'],
             ['notes-postgres-data', 'notes-postgres-backup']),
            (partial(workloads.install_application, app=platform_file.checkout().apps[1]), ['notes-app'], []),
            (workloads.install_keycloak, ['keycloak'], []),
            (partial(workloads.install_shared_proxy, platform=platform_file.checkout()), ['shared-proxy'],
             ['platform-nginx-data']),
        ):
            with self.subTest(function=str(function)), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                directory = base / 'quadlet'
                directory.mkdir()
                runtime = directory / 'platform-kube-runtime'
                rendered = base / 'rendered'
                rendered.mkdir()
                for name in ('todo-postgres', 'keycloak', 'todo-app', 'shared-proxy', 'todo-config',
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
                    # A change to a file the workload installs: its ConfigMap, or Keycloak's
                    # and nginx's own Kube YAML (nginx's ConfigMaps are inside it).
                    config = ('keycloak.yaml' if function == workloads.install_keycloak else
                              'shared-proxy.yaml' if names == ['shared-proxy'] else
                              'notes-config.yaml' if names[0].startswith('notes-') else 'todo-config.yaml')
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
                # Build mode writes the very unit an offline bundle carries.
                units = bundle.quadlets(ROOT, platform_file.checkout(), 8443, '127.0.0.1')
                for name in names:
                    self.assertEqual((runtime / f'{name}.kube').read_bytes(), units[f'{name}.kube'])
                self.assertEqual(list(directory.glob('*.volume')), [directory / 'unrelated.volume'])
                self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
                self.assertEqual((runtime / config).stat().st_mode & 0o777, 0o600)

    def test_an_install_removes_todos_kube_yaml_under_its_old_names(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            directory, rendered = base / 'quadlet', base / 'rendered'
            runtime = directory / 'platform-kube-runtime'
            runtime.mkdir(parents=True)
            rendered.mkdir()
            for name in ('todo-postgres', 'todo-config'):
                (rendered / f'{name}.yaml').write_text(f'fixture: {name}\n')
            for name in ('postgres.yaml', 'config.yaml', 'app.yaml', 'keycloak.yaml'):
                (runtime / name).write_text('old\n')
            with FakeHost():
                workloads.install_postgres(ROOT, directory, runtime, rendered, database=platform_file.checkout().apps[0].database)
            self.assertEqual(sorted(path.name for path in runtime.iterdir()),
                             ['keycloak.yaml', 'todo-config.yaml', 'todo-postgres.kube', 'todo-postgres.yaml'])
            self.assertIn('Yaml=todo-postgres.yaml', (runtime / 'todo-postgres.kube').read_text())

    def test_a_dr_primary_gets_the_bundles_replicated_unit_on_its_own_address(self):
        import offline_bundle
        from app_installer import target_render
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            offline_bundle.build(base / 'bundle', platform_file.checkout().apps)
            target = target_render.load(base / 'bundle', {target_render.PUBLISH_ADDRESS: '192.0.2.10'},
                                        environment={}, recorded={})
            runtime = base / 'quadlet/platform-kube-runtime'
            (base / 'quadlet').mkdir()
            database = apps.KEYCLOAK_DATABASE
            with FakeHost():
                self.assertTrue(workloads.install_postgres(base / 'bundle', base / 'quadlet', runtime, None,
                                                           '192.0.2.10', database=database, target=target))
                unit = (runtime / database.unit).read_text()
                self.assertIn(f'PublishPort=192.0.2.10:{database.replication_port}:5432', unit)
                # Without an address (a standby), the same database publishes only on loopback.
                self.assertTrue(workloads.install_postgres(base / 'bundle', base / 'quadlet', runtime, None,
                                                           database=database, target=target))
                self.assertEqual(re.findall(r'^PublishPort=(.*)$', (runtime / database.unit).read_text(), re.M),
                                 [f'127.0.0.1:{database.replication_port}:5432'])
                with self.assertRaisesRegex(ValueError, 'publish on 192.0.2.10, not on 192.0.2.99'):
                    workloads.install_postgres(base / 'bundle', base / 'quadlet', runtime, None,
                                               '192.0.2.99', database=database, target=target)

    def test_invalid_runtime_directory_fails_before_commands(self):
        with patch('subprocess.run') as run:
            with self.assertRaises(ValueError):
                workloads.install_postgres(ROOT, '/tmp/q', '/tmp/elsewhere', '/tmp/rendered',
                                           database=apps.KEYCLOAK_DATABASE)
            run.assert_not_called()

    def test_shared_proxy_refuses_a_wildcard_publish_address(self):
        # deploy/quadlet/shared-proxy.kube.j2 always keeps a fixed 127.0.0.1
        # binding alongside the requested one; a wildcard address would try
        # to bind the same port twice and podman would refuse to start.
        for wildcard in ('0.0.0.0', '::'):
            with patch('subprocess.run') as run:
                with self.assertRaisesRegex(ValueError, 'wildcard address'):
                    workloads.install_shared_proxy(ROOT, '/tmp/q', '/tmp/q/platform-kube-runtime',
                                                   '/tmp/rendered', publish_address=wildcard,
                                                   platform=platform_file.checkout())
                run.assert_not_called()
        with patch('subprocess.run') as run:
            with self.assertRaises(ValueError):
                workloads.install_shared_proxy(ROOT, '/tmp/q', '/tmp/q/platform-kube-runtime',
                                               '/tmp/rendered', publish_address='not-an-address',
                                               platform=platform_file.checkout())
            run.assert_not_called()

    def test_a_database_without_a_lan_address_publishes_only_on_loopback(self):
        database = platform_file.checkout().apps[0].database
        rendered = quadlet.render(ROOT, 'postgres.kube', workloads.postgres_variables(database)).decode()
        self.assertEqual(rendered.count('PublishPort='), 1)
        self.assertIn('PublishPort=127.0.0.1:5432:5432\n', rendered)

    def test_databases_publish_distinct_ports_from_platform_yaml(self):
        for app in platform_file.checkout().apps:
            rendered = quadlet.render(ROOT, 'postgres.kube',
                                      workloads.postgres_variables(app.database, '192.0.2.50')).decode()
            self.assertIn(f'PublishPort=127.0.0.1:{app.replication_port}:5432\n', rendered)
            self.assertIn(f'PublishPort=192.0.2.50:{app.replication_port}:5432\n', rendered)
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'verified DR group'):
                workloads.install_postgres(ROOT, '/q', '/q/platform-kube-runtime', '/rendered',
                                           publish_address='192.0.2.1',
                                           database=stack.Database(name='third'))
            run.assert_not_called()

    def test_image_build_load_and_identity(self):
        for mode, present, refresh in [('build', False, False), ('build', True, False),
                                       ('build', True, True), ('offline', False, False)]:
            with self.subTest(mode=mode, present=present, refresh=refresh), \
                    FakeHost(images_present=present) as host, tempfile.TemporaryDirectory() as bundle:
                (Path(bundle) / 'images').mkdir()
                platform = platform_file.checkout()
                for image in images.image_list(platform.apps[0]) + images.shared_images(platform):
                    (Path(bundle) / 'images' / image.archive).touch()
                calls = host.calls
                changed = {**images.prepare(ROOT, mode, bundle, refresh, app=platform.apps[0]),
                           **images.prepare_shared(ROOT, mode, bundle, refresh, platform)}
                self.assertEqual(set(changed.values()), {not present or refresh})
                if mode == 'offline':
                    self.assertEqual(sum(a[1] == 'load' for a in calls), 5)
                elif not present or refresh:
                    self.assertEqual(sum(a[1] == 'build' for a in calls), 4)
                    self.assertIn(['podman', 'pull', 'docker.io/library/postgres:17.11'], calls)
