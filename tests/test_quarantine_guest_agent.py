import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = ROOT / 'ansible/.venv/bin/ansible-playbook'


@unittest.skipUnless(ANSIBLE.exists(), 'Ansible test environment required')
class GuestAgentPolicyTests(unittest.TestCase):
    def evaluate(self, policy, expected=None):
        tasks = yaml.safe_load(
            (ROOT / 'ansible/tasks/quarantine-guest-agent.yml').read_text()
        )
        # Exercise the real extraction, validation and output expression,
        # but never run slurp, write configuration or restart a service.
        checks = tasks[1:3]
        if expected is not None:
            checks += [
                {'ansible.builtin.set_fact': {
                    'rendered': tasks[3]['ansible.builtin.lineinfile']['line']}},
                {'ansible.builtin.assert': {'that': ['rendered == expected']}},
            ]
        play = [{'hosts': 'localhost', 'gather_facts': False, 'vars': {
            'todo_quarantine_ga_file': {'content': base64.b64encode(
                policy.encode()).decode()}, 'expected': expected,
        }, 'tasks': checks}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.json'
            path.write_text(json.dumps(play))
            return subprocess.run(
                [str(ANSIBLE), '-i', 'localhost,', '-c', 'local', str(path)],
                capture_output=True, text=True, timeout=30,
            )

    def test_preserves_permissions_and_is_idempotent(self):
        expected = 'FILTER_RPC_ARGS="--allow-rpcs=guest-ping,guest-exec,guest-exec-status"'
        for policy in [
            'FILTER_RPC_ARGS="--allow-rpcs=guest-ping"',
            'FILTER_RPC_ARGS="--allow-rpcs=guest-ping,guest-exec"',
            expected,
        ]:
            with self.subTest(policy=policy):
                result = self.evaluate(policy + '\n', expected)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_refuses_unknown_and_duplicate_assignments(self):
        for policy in [
            'FILTER_RPC_ARGS="--block-rpcs=guest-exec"',
            'FILTER_RPC_ARGS="--allow-rpcs="',
            'FILTER_RPC_ARGS="--allow-rpcs=guest-ping"\nFILTER_RPC_ARGS=""',
        ]:
            with self.subTest(policy=policy):
                result = self.evaluate(policy)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Expected one explicit allow-rpcs', result.stdout)
