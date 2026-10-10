"""Check runnable acceptance examples, without fixing prose or phase layout."""

import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/installer'))

from app_installer import platform_file  # noqa: E402


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
            self.assertEqual(set(command[3:]), set(platform_file.checkout().services()))
        for line in commands.splitlines():
            if "podman exec nginx nginx -t" in line:
                self.assertIn("-c /etc/platform-nginx/nginx.conf", line)


    def test_agent_commands_have_nothing_to_fill_in_by_hand(self):
        # Run 23 stopped because the agent filled in "<todo base-...>" wrongly.
        # Every product, vm, ops and acceptance.py line is used as written.
        guide = (ROOT / 'docs/ACCEPTANCE-AGENT.md').read_text()
        commands = [line for line in guide.splitlines()
                    if re.match(r'(product|vm|ops|\$A) ', line)]
        self.assertTrue(commands)
        for line in commands:
            self.assertNotRegex(line.split('  #')[0], r'<[^<>]+>', line)

    def test_pitr_values_are_read_from_the_logs(self):
        # Run 23 stopped because the agent typed the backup names. The restore
        # lines read every value from a log (helpers.sh backup_name and
        # restore_time, tested in test_acceptance_step.py): Todo goes to the
        # named point from the 08-4 backup, Notes to the time 08-7 printed.
        lines = (ROOT / 'docs/ACCEPTANCE-AGENT.md').read_text().splitlines()
        todo = next(line for line in lines if line.startswith('vm 08-9-'))
        notes = next(line for line in lines if line.startswith('vm 08-10-'))
        mark = next(line for line in lines if line.startswith('vm 08-7-'))
        self.assertIn('--app todo restore --backup $(backup_name todo) --target acceptance_before_after', todo)
        self.assertIn('--app notes restore --target-time $(restore_time) && ', notes)
        self.assertIn("sleep 1 && date --utc +%Y-%m-%dT%H:%M:%SZ && sleep 1", mark)

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
                   if 'python3 -m app_ops' in line and ' rebuild-standby ' in line + ' ']
        self.assertTrue(rebuild)
        for command in rebuild:
            self.assertEqual(command[command.index('--confirm-fenced') + 1], 'todo-primary is fenced')
            self.assertEqual(command[command.index('--confirm-reseed') + 1], 'todo-primary')
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

    def test_registered_group_table_matches_platform_yaml(self):
        guide = (ROOT / 'docs/ACCEPTANCE.md').read_text()
        for service in platform_file.checkout().services():
            self.assertIn(f'`{service}`', guide)
        for database in platform_file.checkout().replicated_databases:
            row = (f'| {database.name} | `{database.container}` | `{database.name}` '
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
        self.assertLessEqual(set(actions), {'get', 'set', 'post', 'delete', 'task', 'exec', 'nic', 'fence'})
        # Fencing and the quarantine stop run through acceptance.py, which calls exactly these.
        self.assertIn('do fence 107', guide)
        self.assertIn('do quarantine-stop 107 todo-primary', guide)
        tool = (ROOT / 'deploy/scripts/lab/acceptance.py').read_text()
        self.assertIn("pve(step, 'fence', vmid)", tool)
        self.assertIn("'/opt/platform/bin/app-quarantine.sh', action, name, step.user", tool)

    def test_every_acceptance_tool_line_in_the_agent_guide_is_a_valid_command(self):
        sys.path.insert(0, str(ROOT / 'deploy/scripts/lab'))
        import acceptance
        guide = (ROOT / 'docs/ACCEPTANCE-AGENT.md').read_text()
        lines = re.findall(r'^\$A --step (\S+) (check|do) (\S+)(.*)$', guide, re.M)
        self.assertGreater(len(lines), 80)
        steps = []
        for step, kind, name, rest in lines:
            arguments = shlex.split(rest.split('#')[0])
            with self.subTest(step=step):
                self.assertRegex(step, r'^[0-9]{2}-[0-9A-Za-z-]+$')
                self.assertIn((kind, name), acceptance.COMMANDS)
                _, validators, _ = acceptance.COMMANDS[kind, name]
                self.assertEqual(len(arguments), len(validators))
                for validate, text in zip(validators, arguments):
                    if not text.startswith('$'):
                        validate(text)
            steps.append(step)
        self.assertEqual(len(steps), len(set(steps)), 'every step label is used once')

    def test_acceptance_reference_links_resolve_in_source(self):
        paths = [ROOT / 'docs' / name for name in (
            'ACCEPTANCE.md', 'ACCEPTANCE-TROUBLESHOOTING.md', 'ACCEPTANCE-AGENT.md', 'PROXMOX-QUARANTINE.md')]
        for path in paths + sorted((ROOT / 'deploy/dr').glob('*.md')):
            for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
                if '://' in target or target.startswith('#'):
                    continue
                self.assertTrue((path.parent / target.split('#')[0]).is_file(), target)


class AppOpsGuideTests(unittest.TestCase):
    """The acceptance guides only use commands, flags and inventories app-ops accepts."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / 'deploy/dr'))
        from app_ops import cli, inventory
        self.cli, self.inventory = cli, inventory
        self.guide = ''.join((ROOT / 'docs' / name).read_text()
                             for name in ('ACCEPTANCE.md', 'PROXMOX-QUARANTINE.md'))

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
        # The nginx-tls commands enter the guide with backlog T4 step 3 (acceptance in provided mode);
        # until then docs/TLS.md and deploy/dr/README.md describe them.
        not_yet = {'sync-standby-secrets', 'nginx-tls-request', 'nginx-tls-install'}
        self.assertEqual(used - not_yet, set(self.cli.COMMANDS) - not_yet)

    def test_example_inventories_load_for_their_commands(self):
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
        # The agent runs them through acceptance.py check browser.
        tool = (ROOT / 'deploy/scripts/lab/acceptance.py').read_text()
        self.assertEqual(set(re.findall(r"'(e2e/test_\w+\.py)'", tool)), flows('ACCEPTANCE.md'))
        self.assertIn('check browser', (ROOT / 'docs/ACCEPTANCE-AGENT.md').read_text())
