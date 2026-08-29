import importlib.util
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "todo_dr.py"
SPEC = importlib.util.spec_from_file_location("todo_dr", SCRIPT)
assert SPEC and SPEC.loader
todo_dr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(todo_dr)


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    def __init__(self, database_outputs):
        self.database_outputs = iter(database_outputs)
        self.commands = []

    def __call__(self, arguments, timeout=None):
        command = list(arguments)
        self.commands.append(command)
        if command[:3] == ["systemctl", "--user", "is-active"]:
            return completed("active\n")
        if command[:3] == ["podman", "inspect", "--format"]:
            return completed("healthy\n")
        if "psql" in command:
            return completed(next(self.database_outputs) + "\n")
        if "pg_ctl" in command:
            return completed("server promoted\n")
        raise AssertionError(f"Unexpected command: {command}")


class TodoDrTests(unittest.TestCase):
    def config(self):
        return todo_dr.Config("todo-primary", "192.0.2.10", "todo-standby", 30)

    def tool(self, outputs, reachable=False):
        runner = FakeRunner(outputs)
        tool = todo_dr.TodoDr(
            self.config(),
            runner=runner,
            connector=lambda address, port, timeout: reachable,
        )
        return tool, runner

    def test_load_legacy_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dr.json"
            path.write_text(
                '{"primary_name":"p","primary_address":"192.0.2.10",'
                '"standby_name":"s","rpo_seconds":30}',
                encoding="utf-8",
            )
            self.assertEqual(todo_dr.load_config(path).rpo_target_seconds, 30)

    def test_status_reports_normal_standby(self):
        tool, _runner = self.tool(["t|on|0/10|0/10|0"], reachable=True)
        output = "\n".join(tool.status_lines())
        self.assertIn("Database role: standby", output)
        self.assertIn("Writable: no", output)
        self.assertIn("Primary endpoint 192.0.2.10:5432: reachable", output)

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_preflight_accepts_fenced_caught_up_standby(self, _hostname):
        tool, _runner = self.tool(["t|on|0/10|0/10|0"])
        status = tool.preflight("todo-primary is fenced")
        self.assertTrue(status.in_recovery)

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_preflight_rejects_reachable_primary(self, _hostname):
        tool, _runner = self.tool(["t|on|0/10|0/10|0"], reachable=True)
        with self.assertRaisesRegex(todo_dr.DrError, "still answers"):
            tool.preflight("todo-primary is fenced")

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_preflight_rejects_missing_lsn(self, _hostname):
        tool, _runner = self.tool(["t|on|||0"])
        with self.assertRaisesRegex(todo_dr.DrError, "LSN is unavailable"):
            tool.preflight("todo-primary is fenced")

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_preflight_rejects_local_apply_lag(self, _hostname):
        tool, _runner = self.tool(["t|on|0/20|0/10|16"])
        with self.assertRaisesRegex(todo_dr.DrError, "unreplayed local WAL"):
            tool.preflight("todo-primary is fenced")

    @mock.patch.object(socket, "gethostname", return_value="wrong-host")
    def test_preflight_rejects_wrong_host(self, _hostname):
        tool, _runner = self.tool([])
        with self.assertRaisesRegex(todo_dr.DrError, "configured standby host"):
            tool.preflight("todo-primary is fenced")

    def test_preflight_requires_exact_fencing_confirmation(self):
        tool, _runner = self.tool([])
        with self.assertRaisesRegex(todo_dr.DrError, "must be exactly"):
            tool.preflight("yes")

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_promote_rechecks_and_verifies_writable_database(self, _hostname):
        tool, runner = self.tool(
            ["t|on|0/10|0/10|0", "f|off|||0"], reachable=False
        )
        status = tool.promote("todo-primary is fenced", "todo-standby")
        self.assertFalse(status.in_recovery)
        flattened = [item for command in runner.commands for item in command]
        self.assertIn("pg_ctl", flattened)

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_promote_requires_exact_standby_confirmation(self, _hostname):
        tool, runner = self.tool(["t|on|0/10|0/10|0"])
        with self.assertRaisesRegex(todo_dr.DrError, "standby hostname"):
            tool.promote("todo-primary is fenced", "wrong-host")
        flattened = [item for command in runner.commands for item in command]
        self.assertNotIn("pg_ctl", flattened)

    def test_load_current_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dr.json"
            path.write_text(
                '{"primary_name":"p","primary_address":"192.0.2.10",'
                '"standby_name":"s","rpo_target_seconds":45}',
                encoding="utf-8",
            )
            self.assertEqual(todo_dr.load_config(path).rpo_target_seconds, 45)

    def test_status_labels_rpo_target_as_informational(self):
        tool, _runner = self.tool(["t|on|0/10|0/10|0"], reachable=True)
        output = "\n".join(tool.status_lines())
        self.assertIn("Configured RPO target (informational)", output)

    def test_control_command_timeout_is_actionable(self):
        def timeout_runner(arguments, timeout=None):
            raise subprocess.TimeoutExpired(arguments, timeout)

        tool = todo_dr.TodoDr(self.config(), runner=timeout_runner)
        with self.assertRaisesRegex(todo_dr.DrError, "timed out"):
            tool.service_status()

if __name__ == "__main__":
    unittest.main()
