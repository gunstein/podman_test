import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, images, quadlet, workloads  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class WorkloadsTests(unittest.TestCase):
    def test_each_workload_is_idempotent_and_preserves_storage(self):
        for function, names, obsolete in (
            (workloads.install_postgres, ['todo-postgres'],
             ['todo-postgres-data', 'todo-postgres-backup']),
            (workloads.install_application, ['todo-app'], []),
            (partial(workloads.install_postgres, app=apps.APPS[1]), ['notes-postgres'],
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
                known = set()
                calls = []

                def command(argv, **kwargs):
                    calls.append((argv, kwargs))
                    rc, stdout = 0, ''
                    if argv == ['podman', 'kube', 'play', '--help']:
                        stdout = '--no-pod-prefix'
                    elif argv[:3] == ['podman', 'secret', 'inspect']:
                        stdout = ' password-with-spaces \n'
                    elif argv[:3] == ['podman', 'secret', 'exists']:
                        rc = 0 if argv[3] in known else 1
                    elif argv[:3] == ['podman', 'secret', 'create']:
                        payload = json.loads(kwargs['input'])
                        self.assertEqual(payload['metadata']['name'], argv[3])
                        for value in payload['data'].values():
                            self.assertEqual(base64.b64decode(value), b' password-with-spaces ')
                        known.add(argv[3])
                    else:
                        self.assertEqual(argv, ['systemctl', '--user', 'daemon-reload'])
                    return subprocess.CompletedProcess(argv, rc, stdout, '')

                with patch('subprocess.run', side_effect=command):
                    self.assertTrue(function(ROOT, directory, runtime, rendered))
                    initial = {p: p.stat().st_mtime_ns for p in runtime.iterdir()}
                    calls.clear()
                    self.assertFalse(function(ROOT, directory, runtime, rendered))
                    self.assertEqual(initial, {p: p.stat().st_mtime_ns for p in runtime.iterdir()})
                    self.assertFalse(any(a[:3] == ['podman', 'secret', 'create'] for a, _ in calls))
                    config = ('keycloak.yaml' if function == workloads.install_keycloak else
                              'notes-config.yaml' if names[0].startswith('notes-') else 'config.yaml')
                    (rendered / config).write_text('changed: true\n')
                    self.assertTrue(function(ROOT, directory, runtime, rendered))
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
            rendered = quadlet.render(ROOT, app.unit('postgres'), {
                'postgres_publish_port': app.replication_port,
                'postgres_publish_address': '192.0.2.50',
            }).decode()
            self.assertIn(f'PublishPort=127.0.0.1:{app.replication_port}:5432\n', rendered)
            self.assertIn(f'PublishPort=192.0.2.50:{app.replication_port}:5432\n', rendered)
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'verified DR group'):
                workloads.install_postgres(ROOT, '/q', '/q/todo-kube-runtime', '/rendered',
                                           publish_address='192.0.2.1',
                                           app=apps.App('third', 'third.test', 'third-frontend'))
            run.assert_not_called()

    def test_image_build_load_and_identity(self):
        for mode, present, refresh in [('build', False, False), ('build', True, False),
                                       ('build', True, True), ('offline', False, False)]:
            calls = []

            def command(argv, **kwargs):
                calls.append(argv)
                rc = int(not present) if argv[1:3] == ['image', 'exists'] else 0
                return subprocess.CompletedProcess(argv, rc,
                                                   '[{"Labels":{"io.todo.proxy":"nginx"}}]', '')

            with self.subTest(mode=mode, present=present, refresh=refresh), \
                    patch('subprocess.run', side_effect=command):
                changed = images.prepare(ROOT, mode, '/bundle', refresh)
                self.assertEqual(set(changed.values()), {not present or refresh})
                if mode == 'offline':
                    self.assertEqual(sum(a[1] == 'load' for a in calls), 5)
                elif not present or refresh:
                    self.assertEqual(sum(a[1] == 'build' for a in calls), 4)
                    self.assertIn(['podman', 'pull', 'docker.io/library/postgres:17.11'], calls)


class RenderingIntegrationTests(unittest.TestCase):
    def test_real_ansible_template_and_kube_runtime_outputs_match(self):
        executable = os.environ.get('ANSIBLE_PLAYBOOK_COMMAND', 'ansible-playbook')
        self.assertIsNotNone(shutil.which(executable), 'Ansible is required for parity verification')
        cases = [
            {'todo_publish_address': '192.0.2.50', 'todo_service_port': 9443},
            {'todo_publish_address': '127.0.0.1', 'todo_service_port': 8443,
             'postgres_publish_address': '192.0.2.50'},
            {'todo_publish_address': '127.0.0.1', 'todo_service_port': 8443},
        ]
        for variables in cases:
            with self.subTest(variables=variables), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                tasks = []
                for template in sorted((ROOT / 'deploy/quadlet').glob('*.kube.j2')):
                    name = template.name.removesuffix('.j2')
                    tasks.append({'name': f'Render {name}', 'ansible.builtin.template': {
                        'src': str(template), 'dest': str(base / name), 'mode': '0644'}})
                playbook = base / 'render.json'
                playbook.write_text(json.dumps([{
                    'hosts': 'localhost', 'gather_facts': False, 'vars': variables, 'tasks': tasks,
                }]))
                subprocess.run([executable, '-i', 'localhost,', '-c', 'local', str(playbook)],
                               check=True, capture_output=True, text=True,
                               env={**os.environ, 'ANSIBLE_LOCAL_TEMP': str(base / 'ansible-tmp')})
                for task in tasks:
                    target = Path(task['ansible.builtin.template']['dest'])
                    self.assertEqual(target.read_bytes(), quadlet.render(ROOT, target.name, variables))
                for profile in ('local', 'prod'):
                    outputs = [base / f'{profile}-{suffix}' for suffix in ('before', 'after')]
                    for output in outputs:
                        subprocess.run([str(ROOT / 'deploy/scripts/render-kube-runtime.sh'),
                                        str(ROOT / f'deploy/environments/{profile}/values.yaml'),
                                        str(output)], check=True, capture_output=True)
                    self.assertEqual({p.name: p.read_bytes() for p in outputs[0].iterdir()},
                                     {p.name: p.read_bytes() for p in outputs[1].iterdir()})
