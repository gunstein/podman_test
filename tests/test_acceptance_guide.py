"""Check runnable acceptance examples, without fixing prose or phase layout."""

import json
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/installer'))

from app_installer import apps  # noqa: E402


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
            self.assertEqual(set(command[3:]), set(apps.services()))
        for line in commands.splitlines():
            if "podman exec nginx nginx -t" in line:
                self.assertIn("-c /etc/todo-nginx/nginx.conf", line)


    def test_direct_mutation_examples_keep_exact_confirmation_arguments(self):
        commands = '\n'.join(shell_blocks()).replace('\\\n', '')
        promotion = [shlex.split(line) for line in commands.splitlines()
                     if 'app_dr.py promote' in line]
        self.assertTrue(promotion)
        for command in promotion:
            self.assertEqual(command[command.index('--confirm-primary-fenced') + 1],
                             'todo-primary is fenced')
            self.assertEqual(command[command.index('--confirm-promotion') + 1],
                             'todo-standby')
        self.assertNotRegex(commands, r'python\S* .*todo_dr_run\.py')
        rebuild = [shlex.split(line) for line in commands.splitlines()
                   if 'deploy/ansible/playbooks/rebuild-standby.yml' in line]
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
            self.assertNotIn('test_auth_adapter.py', command)
            if 'e2e/test_multi_app.py' in command:
                self.assertIn('E2E_MULTI_APP=1', command)
                self.assertIn('E2E_CA_FILE=', command)
            elif 'e2e/test_notes_flow.py' in command:
                self.assertIn('E2E_NOTES_URL=', command)
                self.assertIn('--browser chromium', command)
            else:
                self.assertIn('e2e/test_todo_flow.py', command)
                self.assertIn('--browser chromium', command)
        self.assertTrue(any('e2e/test_todo_flow.py' in line for line in browser))
        self.assertTrue(any('e2e/test_notes_flow.py' in line for line in browser))
        self.assertTrue(any('e2e/test_multi_app.py' in line for line in browser))

    def test_registered_group_table_matches_the_app_registry(self):
        guide = (ROOT / 'docs/ACCEPTANCE.md').read_text()
        for service in apps.services():
            self.assertIn(f'`{service}`', guide)
        for database in apps.REPLICATED_DATABASES:
            row = (f'| {database.name} | `{database.resource("postgres")}` | `{database.name}` '
                   f'| {database.replication_port} | `{database.replication_slot()}` '
                   f'| `{database.replication_slot(rebuilt=True)}` |')
            self.assertIn(row, guide)

    def test_agent_guide_commands_parse_and_stay_within_safety_rules(self):
        guide = (ROOT / 'docs/ACCEPTANCE-AGENT.md').read_text()
        blocks = re.findall(r'```bash\n(.*?)```', guide, re.S)
        self.assertTrue(blocks)
        for block in blocks:
            result = subprocess.run(['bash', '-n'], input=block, text=True,
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        commands = '\n'.join(blocks)
        for unsafe in ('curl -k', '--insecure', 'StrictHostKeyChecking=no',
                       'E2E_IGNORE_HTTPS_ERRORS=true', 'set -x', 'setenforce'):
            self.assertNotIn(unsafe, commands)
        actions = re.findall(r'pve_lab\.py (\w+)', guide)
        self.assertTrue(actions)
        self.assertLessEqual(set(actions), {'get', 'set', 'post', 'delete', 'task', 'exec', 'nic'})
        self.assertIn('app-quarantine.sh stop todo-primary gunstein', guide)

    def test_acceptance_reference_links_resolve_in_source(self):
        for name in ('ACCEPTANCE.md', 'ACCEPTANCE-TROUBLESHOOTING.md', 'ACCEPTANCE-AGENT.md', 'ACCEPTANCE-APP-OPS.md'):
            path = ROOT / 'docs' / name
            for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
                if '://' in target or target.startswith('#'):
                    continue
                self.assertTrue((path.parent / target.split('#')[0]).is_file(), target)


class AppOpsGuideTests(unittest.TestCase):
    """The app-ops acceptance guide only uses commands, flags and inventories app-ops accepts."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / 'deploy/ops'))
        from app_ops import cli, inventory
        self.cli, self.inventory = cli, inventory
        self.guide = (ROOT / 'docs/ACCEPTANCE-APP-OPS.md').read_text()

    def test_commands_parse_and_use_the_inventory_for_their_topology(self):
        blocks = re.findall(r'```bash\n(.*?)```', self.guide, re.S)
        for block in blocks:
            result = subprocess.run(['bash', '-n'], input=block, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        lines = "\n".join(blocks).replace("\\\n", "").splitlines()
        used = set()
        for line in (line for line in lines if 'python3 -m app_ops' in line):
            argv = shlex.split(line.split(';')[0])[3:]
            args = self.cli.parser().parse_args(argv)
            expected = 'initial.yaml' if self.cli.COMMANDS[args.command] == self.cli.INITIAL else 'recovery.yaml'
            self.assertEqual(str(args.inventory), expected, line)
            used.add(args.command)
        self.assertEqual(used - {'sync-standby-secrets'}, set(self.cli.COMMANDS) - {'sync-standby-secrets'})

    def test_example_inventories_load_for_their_commands(self):
        import tempfile
        examples = re.findall(r'```yaml\n(.*?)```', self.guide, re.S)
        self.assertEqual(len(examples), 2)
        for text, roles in zip(examples, (self.cli.INITIAL, self.cli.RECOVERY)):
            with tempfile.NamedTemporaryFile('w', suffix='.yaml') as file:
                file.write(text)
                file.flush()
                hosts = self.inventory.load(file.name, roles)
            self.assertEqual([host.local for host in hosts.values()], [True, False])


class BrowserFlowTests(unittest.TestCase):
    def test_agent_guide_runs_the_same_browser_flows_as_acceptance(self):
        def flows(name):
            return set(re.findall(r'pytest (e2e/test_\w+\.py)', (ROOT / 'docs' / name).read_text()))
        self.assertEqual(flows('ACCEPTANCE.md'), {'e2e/test_todo_flow.py', 'e2e/test_notes_flow.py',
                                                  'e2e/test_multi_app.py'})
        self.assertEqual(flows('ACCEPTANCE-AGENT.md'), flows('ACCEPTANCE.md'))
