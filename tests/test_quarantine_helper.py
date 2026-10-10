import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class QuarantineHelperTests(unittest.TestCase):
    def run_helper(self, action, expected="todo-primary", **settings):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            log = directory / "calls"
            fake = directory / "fake"
            fake.write_text(f'#!{sys.executable}\n' + '''
import os
import sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as output:
    output.write(name + ' ' + ' '.join(args) + '\\n')
if name == 'id':
    print(os.environ.get('ROOT_UID', '0') if len(args) == 1 else '1000')
elif name == 'hostname':
    print('todo-primary')
elif name == 'getent':
    print('gunstein:x:1000:1000::/home/gunstein:/bin/bash')
elif name == 'runuser':
    assert args[:5] == ['-u', 'gunstein', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1000']
    command = args[5:]
    if command[0] == 'env':
        # The platform record is read as the service user, from its own home.
        assert 'HOME=/home/gunstein' in command and command[-3:-1] == ['python3', '-c'], command
        print(os.environ.get('REGISTRY_UNITS', 'shared-proxy.service\\ntodo-app.service\\nnotes-app.service\\nkeycloak.service\\ntodo-postgres.service\\nnotes-postgres.service'))
    elif command[0] == 'podman':
        print(os.environ.get('CONTAINERS', ''), end='')
        sys.exit(int(os.environ.get('PODMAN_RC', '0')))
    elif '--property=Version' in command:
        counter = Path(os.environ['CALLS'] + '.manager')
        seen = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(seen + 1))
        if seen < int(os.environ.get('MANAGER_DOWN', '0')):
            print('Failed to connect to bus: No such file or directory', file=sys.stderr)
            sys.exit(1)
        print('255')
    elif '--property=LoadState' in command:
        print(os.environ.get('LOAD_STATE', 'loaded'))
    elif '--property=ActiveState' in command:
        print(os.environ.get('BAD_ACTIVE_STATE', 'active') if command[3] == os.environ.get('BAD_UNIT') else os.environ.get('ACTIVE_STATE', 'inactive'))
        sys.exit(int(os.environ.get('SHOW_RC', '0')))
    elif '--property=MainPID' in command:
        print(os.environ.get('MAIN_PID', '0'))
    elif '--property=ControlPID' in command:
        print(os.environ.get('CONTROL_PID', '0'))
    elif command[:3] == ['systemctl', '--user', 'stop']:
        sys.exit(int(os.environ.get('STOP_RC', '0')))
    else:
        sys.exit(99)
else:
    sys.exit(99)
''')
            fake.chmod(0o755)
            for name in ("id", "hostname", "runuser", "getent"):
                (directory / name).symlink_to(fake)
            result = subprocess.run(
                ["sh", str(ROOT / "deploy/dr/scripts/app-quarantine.sh"), action, expected, "gunstein"],
                env={**os.environ, "PATH": f"{directory}:{os.environ['PATH']}",
                     "CALLS": str(log), "MANAGER_DELAY": "0", **settings},
                capture_output=True, text=True, check=False,
            )
            return result, log.read_text() if log.exists() else ""

    def test_check_never_stops_services(self):
        result, calls = self.run_helper("check", CONTAINERS="todo-postgres\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("READY:", result.stdout)
        self.assertNotIn("--user stop", calls)

    def test_wrong_host_and_non_root_are_rejected_before_stop(self):
        for settings in ({"expected": "todo-standby"}, {"ROOT_UID": "1000"}):
            result, calls = self.run_helper("stop", **settings)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("--user stop", calls)

    def test_stop_requires_inactive_services_and_no_containers(self):
        result, calls = self.run_helper("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STOPPED:", result.stdout)
        self.assertIn("--user stop shared-proxy.service todo-app.service notes-app.service keycloak.service todo-postgres.service notes-postgres.service", calls)
        for settings in ({"CONTAINERS": "todo-postgres\n"}, {"STOP_RC": "1"},
                         {"PODMAN_RC": "125"},
                         {"ACTIVE_STATE": "activating"}, {"LOAD_STATE": "not-found"}):
            result, _ = self.run_helper("stop", **settings)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("STOPPED:", result.stdout)

    def test_failed_stopped_services_preserve_failure_evidence(self):
        result, calls = self.run_helper("stop", ACTIVE_STATE="failed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STOPPED:", result.stdout)
        self.assertIn("remains failed", result.stderr)
        self.assertNotIn("reset-failed", calls)
        self.assertNotIn("--user start", calls)

    def test_rejects_processes_unknown_states_and_query_errors(self):
        for state in ("inactive", "failed"):
            for settings in ({"MAIN_PID": "123"}, {"CONTROL_PID": "456"},
                             {"MAIN_PID": ""}, {"SHOW_RC": "1"},
                             {"CONTAINERS": "todo-postgres\n"}, {"PODMAN_RC": "125"}):
                with self.subTest(state=state, settings=settings):
                    result, _ = self.run_helper("stop", ACTIVE_STATE=state, **settings)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("STOPPED:", result.stdout)
        for state in ("active", "activating", "deactivating", "reloading", "", "unknown"):
            result, _ = self.run_helper("stop", ACTIVE_STATE=state)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Not stopped:", result.stderr)

    def test_waits_for_the_user_manager_after_boot(self):
        result, calls = self.run_helper('stop', MANAGER_DOWN='3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('STOPPED:', result.stdout)
        manager = [line for line in calls.splitlines() if '--property=Version' in line]
        self.assertEqual(len(manager), 4)
        first_stop = next(i for i, line in enumerate(calls.splitlines()) if 'systemctl --user stop' in line)
        self.assertGreater(first_stop, max(i for i, line in enumerate(calls.splitlines())
                                           if '--property=Version' in line))

    def test_a_manager_that_never_answers_stops_nothing(self):
        for action in ('check', 'stop'):
            with self.subTest(action=action):
                result, calls = self.run_helper(action, MANAGER_DOWN='99', MANAGER_ATTEMPTS='3')
                self.assertEqual(result.returncode, 1)
                self.assertIn('did not answer; nothing was stopped', result.stderr)
                self.assertEqual(calls.count('--property=Version'), 3)
                self.assertNotIn('systemctl --user stop', calls)
                self.assertNotIn('podman', calls)

    def test_notes_service_and_incomplete_registry_cannot_escape_quarantine(self):
        for settings in ({'BAD_UNIT': 'notes-postgres.service'},
                         {'REGISTRY_UNITS': 'shared-proxy.service'},
                         {'REGISTRY_UNITS': 'shared-proxy.service todo-app.service keycloak.service bad;command'}):
            result, _ = self.run_helper('stop', **settings)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('STOPPED:', result.stdout)


class QuarantineRegistryTests(unittest.TestCase):
    def test_the_helper_python_names_every_service_of_the_recorded_platform(self):
        # The fake python3 above never runs the helper's own Python; run it here against
        # the installer it imports on a host, with a platform record in the user's home.
        sys.path.insert(0, str(ROOT / "deploy/installer"))
        from app_installer import apps

        helper = (ROOT / "deploy/dr/scripts/app-quarantine.sh").read_text()
        code = helper.split("python3 -c \\\n  '", 1)[1].split("')", 1)[0]
        with tempfile.TemporaryDirectory() as home:
            record = Path(home) / ".config/platform/platform.json"
            record.parent.mkdir(parents=True)
            record.write_text(json.dumps(apps.registry().to_json()))
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False,
                                    env={**os.environ, "HOME": home, "PYTHONDONTWRITEBYTECODE": "1",
                                         "PYTHONPATH": str(ROOT / "deploy/installer")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), apps.registry().services())
