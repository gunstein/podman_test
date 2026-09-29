"""commands.run: every command has a time limit, and an error shows why it failed, never a secret."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import commands, settings  # noqa: E402


class RunTests(unittest.TestCase):
    def test_a_hanging_command_stops_with_an_error_naming_it(self):
        with self.assertRaisesRegex(RuntimeError, r" -c timed out after 0.2 seconds") as caught:
            commands.run(sys.executable, "-c", "import time; print('secret'); time.sleep(5)", timeout=0.2)
        self.assertNotIn("secret", str(caught.exception))

    def fail(self, code, **kwargs):
        with self.assertRaises(commands.CommandError) as caught:
            commands.run(sys.executable, "-c", code, **kwargs)
        return str(caught.exception)

    def test_a_non_zero_exit_raises_with_the_last_lines_of_stderr(self):
        message = self.fail("import sys; print('one\\ntwo\\nthree\\nfour', file=sys.stderr); sys.exit(3)")
        self.assertIn("failed (exit 3): two / three / four", message)
        self.assertNotIn("one", message)

    def test_stdout_is_shown_when_stderr_is_empty(self):
        self.assertIn(": inactive", self.fail("print('inactive'); raise SystemExit(3)"))

    def test_the_description_names_the_step(self):
        message = self.fail("raise SystemExit(1)", description="todo: PostgreSQL status check")
        self.assertEqual(message, "todo: PostgreSQL status check failed (exit 1)")

    def test_an_allowed_exit_code_returns_the_result(self):
        result = commands.run(sys.executable, "-c", "raise SystemExit(1)", allowed=(0, 1))
        self.assertEqual(result.returncode, 1)

    def test_output_that_can_hold_a_secret_is_never_shown(self):
        code = "import sys; print('ALTER ROLE x PASSWORD s3cret', file=sys.stderr); sys.exit(1)"
        self.assertNotIn("s3cret", self.fail(code, secret_output=True))
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 125, "value", "s3cret")):
            with self.assertRaises(commands.CommandError) as caught:
                commands.run("podman", "secret", "inspect", "--showsecret", "todo-db-password")
        self.assertEqual(str(caught.exception), "podman secret failed (exit 125)")

    def test_a_missing_program_says_so(self):
        with self.assertRaisesRegex(commands.CommandError, "no-such-program is not installed or not on PATH"):
            commands.run("no-such-program", "--version")

    def test_a_program_that_cannot_start_raises_command_error(self):
        # So a caller that catches CommandError (a cleanup after a failure) never
        # lets a PermissionError replace the error it reports.
        with patch("subprocess.run", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaisesRegex(commands.CommandError, "podman rm could not start: .*Permission denied"):
                commands.run("podman", "rm", "--force", "x")

    def test_the_default_limit_is_finite(self):
        self.assertEqual(commands.run.__kwdefaults__["timeout"], settings.COMMAND_TIMEOUT)
        for limit in (settings.COMMAND_TIMEOUT, settings.HEALTH_TIMEOUT, settings.IMAGE_TIMEOUT,
                      settings.DATA_COPY_TIMEOUT):
            self.assertTrue(0 < limit < 24 * 3600)


if __name__ == "__main__":
    unittest.main()
