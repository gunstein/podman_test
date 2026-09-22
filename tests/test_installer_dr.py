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
            for workload, fact in [('postgres', 'todo_postgres_kube_changed'),
                                   ('application', 'todo_application_kube_changed'),
                                   ('shared-proxy', 'todo_proxy_kube_changed')]:
                for expected in ('true', 'false'):
                    tasks.extend([
                        {'name': 'Install ' + workload,
                         'ansible.builtin.include_tasks': str(ROOT / 'deploy/ansible/tasks/install-workload.yml'),
                         'vars': {'todo_installer_workload': workload}},
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
                'todo-postgres.kube', 'todo-keycloak.kube', 'todo-app.kube', 'shared-proxy.kube'})
            for path in runtime.iterdir():
                self.assertEqual(path.read_bytes(), (RUNTIME / path.name).read_bytes())
