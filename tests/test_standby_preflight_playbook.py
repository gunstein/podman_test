"""Run the real standby preflight playbook against two local hosts with separate fake identities."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/installer'))
from app_installer import apps  # noqa: E402

HOSTS = {'primary': ('192.0.2.10', 'a' * 32), 'standby': ('192.0.2.20', 'b' * 32)}
VOLUMES = [database.volume('data') for database in apps.REPLICATED_DATABASES]


def script(path, body):
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(0o755)


class StandbyPreflightPlaybookTests(unittest.TestCase):
    def run_preflight(self, base, volumes, addresses=None):
        fixture = (ROOT / 'tests/fake_replication_runtime.py').read_text()
        inventory = []
        for host, (address, machine_id) in HOSTS.items():
            binaries = base / host / 'bin'
            binaries.mkdir(parents=True)
            for name in ('podman', 'systemctl'):
                (binaries / name).write_text(f'#!{sys.executable}\n' + fixture)
                (binaries / name).chmod(0o755)
            script(binaries / 'hostname', f'echo {host}\n')
            script(binaries / 'cat', f'[ "$*" = /etc/machine-id ] && echo {machine_id} && exit 0\nexec /bin/cat "$@"\n')
            reported = (addresses or {}).get(host, address)
            script(binaries / 'ip', f'echo "2: eth0    inet {reported}/24 scope global eth0"\n')
            script(binaries / 'firewall-cmd', 'exit 0\n')
            state = {'secrets': [], 'roles': [], 'hba': [], 'volumes': volumes[host],
                     'standbys': [], 'commands': []}
            (base / host / 'state.json').write_text(json.dumps(state))
            interpreter = base / host / 'python'
            script(interpreter, f'export PATH="{binaries}:$PATH" REPLICATION_TEST_STATE="{base / host / "state.json"}"\n'
                                f'exec "{sys.executable}" "$@"\n')
            inventory.append(
                f'{host} ansible_connection=local ansible_python_interpreter={interpreter} '
                f'ansible_become_exe={base / "become"} todo_user_home={base / host / "home"} '
                f'todo_node_role={host} todo_node_address={address}')
        script(base / 'become', 'for arg do last=$arg; done\nexec /bin/sh -c "$last"\n')
        (base / 'hosts.ini').write_text(
            f'[todo_primary]\n{inventory[0]}\n[todo_standby]\n{inventory[1]}\n'
            '[todo_cluster:children]\ntodo_primary\ntodo_standby\n')
        return subprocess.run(
            [os.environ.get('ANSIBLE_PLAYBOOK', 'ansible-playbook'), '-i', str(base / 'hosts.ini'),
             str(ROOT / 'deploy/ansible/playbooks/preflight-standby.yml')],
            capture_output=True, text=True,
            env={**os.environ, 'ANSIBLE_BECOME_ALLOW_SAME_USER': 'true'})

    def test_valid_initial_pair_passes_without_changing_runtime_state(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            result = self.run_preflight(base, {'primary': VOLUMES, 'standby': []})
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn('Standby preflight passed for primary (192.0.2.10) and standby (192.0.2.20)', output)
            for host in HOSTS:
                commands = json.loads((base / host / 'state.json').read_text())['commands']
                self.assertFalse([c for c in commands if c[:2] in (['podman', 'volume'], ['podman', 'secret'])
                                  and c[2] not in ('exists', 'inspect')])

    def test_existing_standby_data_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_preflight(Path(directory), {'primary': VOLUMES, 'standby': VOLUMES[-1:]})
            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn('will not overwrite', output)
            self.assertIn(VOLUMES[-1], output)

    def test_wrong_declared_address_stops_before_the_pair_check(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_preflight(Path(directory), {'primary': VOLUMES, 'standby': []},
                                        addresses={'standby': '192.0.2.99'})
            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn('Host identity does not match', output)
            self.assertNotIn('Validate the primary and standby relationship', output)


if __name__ == '__main__':
    unittest.main()
