"""wait-ready.sh against fake systemctl, podman, curl and sleep commands."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/scripts/wait-ready.sh"
# What the callers pass for today's platform (apps.Platform.ready, tested in test_apps).
DATABASES = "todo-postgres notes-postgres keycloak-postgres"
ARGUMENTS = {
    "app": ["app", f"{DATABASES} keycloak todo-app notes-app shared-proxy",
            f"{DATABASES} keycloak todo-backend todo-frontend notes-backend notes-frontend nginx",
            "todo.test/ready", "notes.test/ready"],
    "standby": ["standby", DATABASES, DATABASES],
}

# Each fake reads its last argument (unit, container or URL options) and fails
# for the names listed in the matching environment variable.
FAKES = {
    "systemctl": 'for a; do last=$a; done\n'
                 'case " $INACTIVE " in *" ${last%.service} "*) exit 3;; esac',
    "podman": 'for a; do last=$a; done\n'
              'case " $ABSENT " in *" $last "*) exit 125;; esac\n'
              'case " $STARTING " in *" $last "*) echo "true starting"; exit;; esac\n'
              'case $last in *-frontend) echo "true ";; *) echo "true healthy";; esac',
    "curl": 'echo "$*" >> "$CURL_LOG"\ncase " $* " in *"Host: $NOT_READY "*) exit 22;; esac',
    "sleep": "exit 0",
}


def run(mode, **faults):
    with tempfile.TemporaryDirectory() as directory:
        for name, body in FAKES.items():
            fake = Path(directory) / name
            fake.write_text("#!/bin/bash\n" + body + "\n")
            fake.chmod(0o755)
        environment = {"PATH": f"{directory}:/usr/bin:/bin", "WAIT_TIMEOUT": "0", "CURL_LOG": "/dev/null",
                       "INACTIVE": "", "ABSENT": "", "STARTING": "", "NOT_READY": "none"}
        environment.update(faults)
        arguments = ARGUMENTS.get(mode[0], mode) if len(mode) == 1 else mode
        return subprocess.run(["bash", str(SCRIPT), *arguments], capture_output=True, text=True,
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
                 ({"NOT_READY": "notes.test"}, "readiness of notes.test/ready"))
        for faults, message in cases:
            with self.subTest(message):
                result = run(["app"], **faults)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"NOT READY after 0s: {message}", result.stdout)

    def test_each_url_is_an_apps_hostname_and_its_own_ready_path(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "curl.log"
            result = run(["app", "nginx", "nginx", "todo.test/ready", "help.test/status/ok"], CURL_LOG=str(log))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual([line.split()[-2:] for line in log.read_text().splitlines()],
                             [["todo.test", "http://127.0.0.1:8080/ready"],
                              ["help.test", "http://127.0.0.1:8080/status/ok"]])

    def test_a_standby_checks_only_its_databases(self):
        result = run(["standby"], INACTIVE="shared-proxy", ABSENT="nginx", NOT_READY="todo.test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = run(["standby"], STARTING="keycloak-postgres")
        self.assertEqual(result.returncode, 1)

    def test_usage_needs_a_mode(self):
        self.assertEqual(run([]).returncode, 2)
        self.assertEqual(run(["primary"]).returncode, 2)

    def test_names_come_from_the_caller(self):
        self.assertEqual(run(["app"]).returncode, 0)
        for arguments in (["app"], ["app", "a"], ["standby", "a"], ["dev", "a", "b"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(["bash", str(SCRIPT), *arguments], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)
        self.assertNotRegex(SCRIPT.read_text(), r"(?<![a-z])(todo|notes)(?![a-z])")


if __name__ == "__main__":
    unittest.main()
