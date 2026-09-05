import os
import subprocess
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
            fake.write_text('''#!/usr/bin/env python3
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
elif name == 'runuser':
    assert args[:5] == ['-u', 'gunstein', '--', 'env', 'XDG_RUNTIME_DIR=/run/user/1000']
    command = args[5:]
    if command[0] == 'podman':
        print(os.environ.get('CONTAINERS', ''), end='')
    elif '--property=LoadState' in command:
        print(os.environ.get('LOAD_STATE', 'loaded'))
    elif '--property=ActiveState' in command:
        print(os.environ.get('ACTIVE_STATE', 'inactive'))
    elif command[:3] == ['systemctl', '--user', 'stop']:
        sys.exit(int(os.environ.get('STOP_RC', '0')))
    else:
        sys.exit(99)
else:
    sys.exit(99)
''')
            fake.chmod(0o755)
            for name in ("id", "hostname", "runuser"):
                (directory / name).symlink_to(fake)
            result = subprocess.run(
                ["sh", str(ROOT / "scripts/todo-quarantine.sh"), action, expected, "gunstein"],
                env={**os.environ, "PATH": f"{directory}:{os.environ['PATH']}",
                     "CALLS": str(log), **settings},
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
        self.assertIn("--user stop todo-app.service todo-keycloak.service todo-postgres.service", calls)
        for settings in ({"CONTAINERS": "todo-postgres\n"}, {"STOP_RC": "1"},
                         {"ACTIVE_STATE": "activating"}, {"LOAD_STATE": "not-found"}):
            result, _ = self.run_helper("stop", **settings)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("STOPPED:", result.stdout)
