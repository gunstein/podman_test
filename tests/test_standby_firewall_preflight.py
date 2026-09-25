"""Exercise the real firewalld preflight gate without touching a live firewall."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_firewall_block():
    tasks = yaml.safe_load((ROOT / 'deploy/ansible/roles/standby_preflight/tasks/main.yml').read_text())
    return next(t for t in tasks if t['name'].startswith('Require an active firewall'))


class StandbyFirewallPreflightTests(unittest.TestCase):
    def run_probe(self, directory, firewalld_state_rc, rich_rule_rc):
        firewall_cmd = directory / 'firewall-cmd'
        firewall_cmd.write_text(
            '#!/bin/sh\n'
            'case "$*" in\n'
            '  *--state*) exit ' + str(firewalld_state_rc) + ' ;;\n'
            '  *--query-rich-rule=*) exit ' + str(rich_rule_rc) + ' ;;\n'
            'esac\n'
            'exit 1\n'
        )
        firewall_cmd.chmod(0o755)
        become = directory / 'become'
        become.write_text('#!/bin/sh\nfor arg do last=$arg; done\nexec /bin/sh -c "$last"\n')
        become.chmod(0o755)

        inventory = directory / 'inventory.yml'
        inventory.write_text(yaml.safe_dump({'all': {
            'children': {
                'todo_primary': {'hosts': {'primary': {'todo_node_address': '192.0.2.1'}}},
                'todo_standby': {'hosts': {'standby': {'todo_node_address': '192.0.2.2'}}},
            },
            'vars': {
                'ansible_connection': 'local',
                'ansible_python_interpreter': sys.executable,
                'ansible_become_exe': str(become),
            },
        }}))
        probe = directory / 'probe.yml'
        probe.write_text(yaml.safe_dump([{
            'name': 'Exercise the firewalld preflight gate',
            'hosts': 'todo_primary',
            'gather_facts': False,
            'vars': {
                'todo_node_role': 'primary',
                'todo_replication_metadata': [{'replication_port': p} for p in (5432, 5433, 5434)],
            },
            'tasks': [load_firewall_block()],
        }]))
        return subprocess.run(
            [os.environ.get('ANSIBLE_PLAYBOOK', 'ansible-playbook'), '-i', str(inventory), str(probe)],
            cwd=directory, capture_output=True, text=True, timeout=60,
            env={**os.environ, 'PATH': str(directory) + ':' + os.environ['PATH'],
                 'ANSIBLE_BECOME_ALLOW_SAME_USER': 'true',
                 'ANSIBLE_LOCAL_TEMP': str(directory / 'ansible-local')},
        )

    def test_inactive_firewalld_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_probe(Path(temp), firewalld_state_rc=1, rich_rule_rc=0)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_rich_rule_fails_with_the_exact_command_to_run(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_probe(Path(temp), firewalld_state_rc=0, rich_rule_rc=1)
            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn('firewall-cmd --permanent --zone=public --add-rich-rule', output)
            self.assertIn('source address=\\"192.0.2.2/32\\"', output)
            self.assertIn('destination address=\\"192.0.2.1\\"', output)
            self.assertIn('port=\\"5432-5434\\"', output)

    def test_active_firewalld_with_the_documented_rule_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_probe(Path(temp), firewalld_state_rc=0, rich_rule_rc=0)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
