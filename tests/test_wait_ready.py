"""wait-ready.sh against fake systemctl, podman, curl and sleep commands."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/scripts/wait-ready.sh"

# Each fake reads its last argument (unit, container or URL options) and fails
# for the names listed in the matching environment variable.
FAKES = {
    "systemctl": 'for a; do last=$a; done\n'
                 'case " $INACTIVE " in *" ${last%.service} "*) exit 3;; esac',
    "podman": 'for a; do last=$a; done\n'
              'case " $ABSENT " in *" $last "*) exit 125;; esac\n'
              'case " $STARTING " in *" $last "*) echo "true starting"; exit;; esac\n'
              'case $last in *-frontend) echo "true ";; *) echo "true healthy";; esac',
    "curl": 'case " $* " in *"Host: $NOT_READY "*) exit 22;; esac',
    "sleep": "exit 0",
}


def run(mode, **faults):
    with tempfile.TemporaryDirectory() as directory:
        for name, body in FAKES.items():
            fake = Path(directory) / name
            fake.write_text("#!/bin/bash\n" + body + "\n")
            fake.chmod(0o755)
        environment = {"PATH": f"{directory}:/usr/bin:/bin", "WAIT_TIMEOUT": "0",
                       "INACTIVE": "", "ABSENT": "", "STARTING": "", "NOT_READY": "none"}
        environment.update(faults)
        return subprocess.run(["bash", str(SCRIPT), *mode], capture_output=True, text=True,
                              env=environment, timeout=30)


class WaitReadyTests(unittest.TestCase):
    def test_everything_up_is_ready(self):
        for mode in ("app", "standby"):
            result = run([mode])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f"READY: {mode}", result.stdout)

    def test_the_first_missing_piece_is_named(self):
        cases = (({"INACTIVE": "shared-proxy"}, "service shared-proxy"),
                 ({"ABSENT": "nginx"}, "container nginx"),
                 ({"STARTING": "todo-postgres"}, "container todo-postgres (true starting)"),
                 ({"NOT_READY": "notes.test"}, "readiness of notes.test"))
        for faults, message in cases:
            with self.subTest(message):
                result = run(["app"], **faults)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"NOT READY after 0s: {message}", result.stdout)

    def test_a_standby_checks_only_its_databases(self):
        result = run(["standby"], INACTIVE="shared-proxy", ABSENT="nginx", NOT_READY="todo.test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = run(["standby"], STARTING="keycloak-postgres")
        self.assertEqual(result.returncode, 1)

    def test_usage_needs_a_mode(self):
        self.assertEqual(run([]).returncode, 2)
        self.assertEqual(run(["primary"]).returncode, 2)


if __name__ == "__main__":
    unittest.main()
