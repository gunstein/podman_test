"""Exercise the real rebuild privilege gate without touching a database or sudo."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class RebuildPrivilegeTests(unittest.TestCase):
    def test_privilege_failure_on_either_host_stops_all_later_plays(self):
        plays = yaml.safe_load((ROOT / 'ansible/rebuild-standby.yml').read_text())
        gate = plays[0]
        self.assertEqual(gate['hosts'], 'todo_rebuild_standby')
        self.assertTrue(gate['any_errors_fatal'])
        self.assertEqual(plays[1]['ansible.builtin.import_playbook'],
                         'preflight-standby-rebuild.yml')
        self.assertEqual(len(gate['tasks']), 2)
        for task in gate['tasks']:
            self.assertTrue(task['become'])
            self.assertEqual(task['become_user'], 'root')
            self.assertFalse(task['changed_when'])
        self.assertEqual(gate['tasks'][0]['delegate_to'], 'localhost')
        self.assertNotIn('delegate_to', gate['tasks'][1])

        for denied in ('controller', 'target', None):
            with self.subTest(denied=denied), tempfile.TemporaryDirectory() as directory:
                tmp = Path(directory)
                # Simulate only the escalation transport. Real Ansible executes
                # the gate and propagates failures across subsequent plays.
                allow = tmp / 'allow'
                allow.write_text('#!/bin/sh\nfor arg do last=$arg; done\n'
                                 'exec /bin/sh -c "$last"\n')
                deny = tmp / 'deny'
                deny.write_text('#!/bin/sh\necho "sudo: a password is required" >&2\n'
                                'exit 1\n')
                allow.chmod(0o755)
                deny.chmod(0o755)
                hosts = {}
                for host, side in (('localhost', 'controller'), ('rebuilt', 'target')):
                    hosts[host] = {
                        'ansible_connection': 'local',
                        'ansible_python_interpreter': sys.executable,
                        'ansible_become_exe': str(deny if denied == side else allow),
                    }
                inventory = tmp / 'inventory.yml'
                inventory.write_text(yaml.safe_dump({'all': {
                    'hosts': hosts,
                    'children': {'todo_rebuild_standby': {'hosts': {'rebuilt': {}}}},
                }}))
                probe = tmp / 'probe.yml'
                probe.write_text(yaml.safe_dump([gate, {
                    'name': 'Sentinel for later primary and rebuild changes',
                    'hosts': 'all', 'gather_facts': False,
                    'tasks': [{'name': 'Record reaching a later play',
                               'ansible.builtin.copy': {
                                   'content': 'reached',
                                   'dest': str(tmp / 'later-{{ inventory_hostname }}'),
                                   'mode': '0600',
                               }}],
                }]))
                result = subprocess.run(
                    [os.environ.get('ANSIBLE_PLAYBOOK', 'ansible-playbook'),
                     '-i', str(inventory), str(probe)],
                    cwd=tmp, capture_output=True, text=True, timeout=60,
                    env={**os.environ, 'ANSIBLE_BECOME_ALLOW_SAME_USER': 'true',
                         'ANSIBLE_LOCAL_TEMP': str(tmp / 'ansible-local')},
                )
                output = result.stdout + result.stderr
                reached = list(tmp.glob('later-*'))
                if denied:
                    self.assertNotEqual(result.returncode, 0, output)
                    self.assertIn('sudo: a password is required', output)
                    self.assertEqual(reached, [], output)
                else:
                    self.assertEqual(result.returncode, 0, output)
                    self.assertEqual(len(reached), 2, output)
