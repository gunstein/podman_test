"""Check runnable acceptance examples, without fixing prose or phase layout."""

import json
import re
import shlex
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def shell_blocks():
    guide = (ROOT / 'docs/ACCEPTANCE.md').read_text()
    return re.findall(r'```bash\n(.*?)```', guide, re.S)


class AcceptanceGuideTests(unittest.TestCase):
    def test_shell_examples_parse_without_executing_them(self):
        for block in shell_blocks():
            result = subprocess.run(
                ['bash', '-n'], input=block, text=True, capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        commands = "\n".join(shell_blocks()).replace("\\\n", "")
        checks = [shlex.split(line) for line in commands.splitlines()
                  if "systemctl --user is-active" in line]
        self.assertTrue(checks)
        for command in checks:
            self.assertEqual(set(command[3:]), {
                "todo-app.service", "todo-keycloak.service",
                "todo-postgres.service", "shared-proxy.service",
            })
        for line in commands.splitlines():
            if "podman exec nginx nginx -t" in line:
                self.assertIn("-c /etc/todo-nginx/nginx.conf", line)


    def test_direct_mutation_examples_keep_exact_confirmation_arguments(self):
        commands = '\n'.join(shell_blocks()).replace('\\\n', '')
        promotion = [shlex.split(line) for line in commands.splitlines()
                     if 'todo_dr.py promote' in line]
        self.assertTrue(promotion)
        for command in promotion:
            self.assertEqual(command[command.index('--confirm-primary-fenced') + 1],
                             'todo-primary is fenced')
            self.assertEqual(command[command.index('--confirm-promotion') + 1],
                             'todo-standby')
        self.assertNotRegex(commands, r'python\S* .*todo_dr_run\.py')
        rebuild = [shlex.split(line) for line in commands.splitlines()
                   if 'ansible/rebuild-standby.yml' in line]
        self.assertTrue(rebuild)
        for command in rebuild:
            self.assertIn('--ask-become-pass', command)
            values = json.loads(command[command.index('--extra-vars') + 1])
            self.assertEqual(values['todo_confirm_old_primary_fenced'],
                             'todo-primary is fenced')
            self.assertEqual(values['todo_confirm_reseed'], 'todo-primary')
        self.assertNotIn('--start-at-task', commands)
        self.assertNotIn('--replace', commands)

    def test_browser_command_uses_real_flow_with_tls_verification(self):
        commands = '\n'.join(shell_blocks()).replace('\\\n', '')
        browser = [line for line in commands.splitlines()
                   if '-m pytest' in line]
        self.assertTrue(browser)
        for command in browser:
            self.assertIn('E2E_IGNORE_HTTPS_ERRORS=false', command)
            self.assertIn('e2e/test_todo_flow.py', command)
            self.assertIn('--browser chromium', command)
            self.assertNotIn('test_auth_adapter.py', command)

    def test_acceptance_reference_links_resolve_in_source(self):
        for name in ('ACCEPTANCE.md', 'ACCEPTANCE-TROUBLESHOOTING.md'):
            path = ROOT / 'docs' / name
            for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
                if '://' in target or target.startswith('#'):
                    continue
                self.assertTrue((path.parent / target.split('#')[0]).is_file(), target)
