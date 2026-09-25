"""Execute the DR transport and Python installer locally with inert runtime commands."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from tests.runtime_fixture import ROOT, RUNTIME


class DRInstallerTests(unittest.TestCase):
    def test_controller_files_are_staged_and_target_python_reports_idempotency(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            binaries = base / 'bin'
            binaries.mkdir()
            for name in ('podman', 'systemctl'):
                stub = binaries / name
                stub.write_text(
                    f'#!{sys.executable}\n'
                    'import sys\n'
                    "args = sys.argv[1:]\n"
                    "if args == ['kube', 'play', '--help']: print('--no-pod-prefix')\n"
                    "elif args[:2] == ['secret', 'inspect']: print('fixture-password')\n"
                    "elif args[:2] == ['secret', 'exists']: pass\n"
                    "elif args == ['--user', 'daemon-reload']: pass\n"
                    "elif args == ['is-active', 'fapolicyd']: raise SystemExit(3)\n"
                    'else: raise SystemExit(99)\n'
                )
                stub.chmod(0o755)
            tasks = []
            for workload, fact, app in [
                ('postgres', 'todo_postgres_kube_changed', 'todo'),
                ('application', 'todo_application_kube_changed', 'todo'),
                ('postgres', 'todo_postgres_kube_changed', 'notes'),
                ('application', 'todo_application_kube_changed', 'notes'),
                ('shared-proxy', 'todo_proxy_kube_changed', 'todo'),
            ]:
                for expected in ('true', 'false'):
                    tasks.extend([
                        {'name': 'Install ' + workload,
                         'ansible.builtin.include_tasks': str(ROOT / 'deploy/ansible/tasks/install-workload.yml'),
                         'vars': {'app_installer_workload': workload, 'app_installer_app': app}},
                        {'name': 'Check change result', 'ansible.builtin.assert': {
                            'that': [f'{fact} == {expected}']}},
                    ])
            playbook = base / 'probe.yml'
            playbook.write_text(yaml.safe_dump([{
                'hosts': 'localhost', 'gather_facts': False,
                'environment': {'PATH': str(binaries) + ':' + os.environ['PATH']},
                'vars': {'project_root': str(ROOT), 'todo_user_home': str(base / 'target'),
                         'todo_rendered_manifest_directory': str(RUNTIME),
                         'todo_publish_address': '192.0.2.10', 'todo_service_port': 8443},
                'tasks': tasks,
            }]))
            result = subprocess.run([os.environ.get('ANSIBLE_PLAYBOOK', 'ansible-playbook'),
                                     '-i', 'localhost,', '-c', 'local', str(playbook)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            runtime = base / 'target/.config/containers/systemd/todo-kube-runtime'
            self.assertEqual({p.name for p in runtime.glob('*.kube')}, {
                'todo-postgres.kube', 'keycloak.kube', 'todo-app.kube', 'shared-proxy.kube',
                'notes-postgres.kube', 'notes-app.kube'})
            for path in runtime.iterdir():
                expected = (RUNTIME / path.name).read_bytes()
                self.assertEqual(path.read_bytes(), expected)


class ReplicationBridgeTests(unittest.TestCase):
    def probe(self, base, tasks, standby=False):
        binaries = base / 'bin'
        binaries.mkdir()
        fixture = (ROOT / 'tests/fake_replication_runtime.py').read_text()
        for name in ('podman', 'systemctl'):
            path = binaries / name
            path.write_text(f'#!{sys.executable}\n' + fixture)
            path.chmod(0o755)
        playbook = base / 'probe.yml'
        playbook.write_text(yaml.safe_dump([{
            'hosts': 'localhost', 'gather_facts': False,
            'environment': {'PATH': str(binaries) + ':' + os.environ['PATH'],
                            'REPLICATION_TEST_STATE': str(base / 'state.json'),
                            'REPLICATION_TEST_STANDBY': '1' if standby else '0'},
            'vars': {'project_root': str(ROOT), 'todo_user_home': str(base / 'target'),
                     'todo_rendered_manifest_directory': str(RUNTIME),
                     'todo_node_address': '192.0.2.50',
                     'todo_replication_primary_address': '192.0.2.50'},
            'tasks': tasks,
        }]))
        return subprocess.run([os.environ.get('ANSIBLE_PLAYBOOK', 'ansible-playbook'),
                               '-i', 'localhost,', '-c', 'local', str(playbook)],
                              capture_output=True, text=True)

    def bridge(self, operation, app):
        return {'name': f'{operation} for {app}', 'ansible.builtin.include_tasks':
                str(ROOT / 'deploy/ansible/tasks/replicate-workload.yml'),
                'vars': {'todo_replication_operation': operation, 'todo_replication_app': app}}

    def test_replication_bridge_reports_idempotent_primary_and_status(self):
        from app_installer import apps
        tasks = []
        for app in apps.APPS:
            for expected in ('true', 'false'):
                tasks += [self.bridge('primary', app.name), {
                    'name': 'Check replication change result', 'ansible.builtin.assert': {
                        'that': [f'(todo_replication_result.stdout | from_json).changed == {expected}']}}]
            tasks += [self.bridge('status', app.name), {
                'name': 'Check read-only status operation', 'ansible.builtin.assert': {
                    'that': ['not (todo_replication_result.stdout | from_json).changed',
                             'not (todo_replication_result.stdout | from_json).status.in_recovery']}}]
        with tempfile.TemporaryDirectory() as directory:
            result = self.probe(Path(directory), tasks)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_standby_bridge_stages_pvc_and_refuses_destructive_repeat(self):
        import json

        from app_installer import apps
        for app in apps.APPS:
            with self.subTest(app=app.name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                repeated = {'name': 'Expect existing-data refusal', 'block': [self.bridge('standby', app.name),
                    {'name': 'Unexpected successful repeat', 'ansible.builtin.fail': {'msg': 'overwrote data'}}],
                    'rescue': [{'name': 'Require safe bootstrap refusal', 'ansible.builtin.assert': {
                        'that': ["'never overwrites' in ansible_failed_result.stderr"]}}]}
                result = self.probe(base, [self.bridge('standby', app.name), repeated], standby=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                state = json.loads((base / 'state.json').read_text())
                self.assertEqual(state['volumes'], [app.volume('data')])
                self.assertEqual(state['commands'].count(['podman', 'volume', 'exists', app.volume('data')]), 2,
                                 'A failed mutating operation must never be retried automatically')
                self.assertEqual(sum('pg_basebackup' in command for command in state['commands']), 1)
                self.assertFalse(any(command[:3] == ['podman', 'volume', 'rm'] for command in state['commands']))
                unit = base / 'target/.config/containers/systemd/todo-kube-runtime' / app.unit('postgres')
                self.assertTrue(unit.exists())
