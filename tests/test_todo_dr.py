import importlib.util
import json
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "deploy/scripts" / "todo_dr.py"
SPEC = importlib.util.spec_from_file_location("todo_dr", SCRIPT)
assert SPEC and SPEC.loader
todo_dr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(todo_dr)


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    def __init__(self, database_outputs):
        self.database_outputs = iter(database_outputs)
        self.current = next(self.database_outputs, None)
        self.commands = []

    def __call__(self, arguments, timeout=None):
        command = list(arguments)
        self.commands.append(command)
        if command[:3] == ["systemctl", "--user", "is-active"]:
            return completed("active\n")
        if command[:3] == ["podman", "inspect", "--format"]:
            return completed("healthy\n")
        if "psql" in command:
            return completed(self.current + "\n")
        if "pg_ctl" in command:
            self.current = next(self.database_outputs)
            return completed("server promoted\n")
        raise AssertionError(f"Unexpected command: {command}")


class TodoDrTests(unittest.TestCase):
    def setUp(self):
        registry = mock.patch.object(todo_dr.apps, 'REPLICATED_DATABASES', (todo_dr.apps.APPS[0],))
        registry.start()
        self.addCleanup(registry.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.journal = Path(self.temporary.name) / 'promotion.json'

    def config(self):
        return todo_dr.Config("todo-primary", "192.0.2.10", "todo-standby", 30)

    def tool(self, outputs, reachable=False):
        runner = FakeRunner(outputs)
        tool = todo_dr.TodoDr(
            self.config(),
            runner=runner,
            connector=lambda address, port, timeout: reachable,
            journal_path=self.journal,
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
        self.assertTrue(status["todo"].in_recovery)

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
        self.assertFalse(status["todo"].in_recovery)
        self.assertEqual(json.loads(self.journal.read_text())["state"], "complete")
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

class GroupPromotionTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.journal = Path(self.temporary.name) / 'promotion.json'
        self.apps = (todo_dr.apps.APPS[0], replace(todo_dr.apps.APPS[1], replication_port=5433))
        self.registry = mock.patch.object(todo_dr.apps, 'REPLICATED_DATABASES', self.apps)
        self.registry.start()
        self.addCleanup(self.registry.stop)
        self.hostname = mock.patch.object(socket, 'gethostname', return_value='standby')
        self.hostname.start()
        self.addCleanup(self.hostname.stop)
        self.states = {app.resource('postgres'): 't|on|0/10|0/10|0' for app in self.apps}
        self.commands = []
        self.endpoints = []
        self.failed_promotion = None

    def runner(self, arguments, timeout=None):
        self.commands.append(list(arguments))
        if arguments[:3] == ['systemctl', '--user', 'is-active']:
            return completed('active')
        if arguments[:3] == ['podman', 'inspect', '--format']:
            return completed('healthy')
        if 'psql' in arguments:
            return completed(self.states[arguments[2]])
        if 'pg_ctl' in arguments:
            # A durable decision naming BOTH apps must precede the first promotion.
            decision = json.loads(self.journal.read_text())
            self.assertEqual(decision['applications'], ['todo', 'notes'])
            self.assertEqual(decision['state'], 'promoting')
            if arguments[2] == self.failed_promotion:
                return completed(stderr='injected promotion failure', returncode=1)
            self.states[arguments[2]] = 'f|off|||0'
            return completed()
        raise AssertionError(arguments)

    def tool(self, reachable_port=None):
        def connect(address, port, timeout):
            self.endpoints.append(port)
            return port == reachable_port
        return todo_dr.TodoDr(todo_dr.Config('primary', '192.0.2.50', 'standby', 30, ('todo', 'notes')),
                              runner=self.runner, connector=connect, journal_path=self.journal)

    def test_last_app_lag_prevents_every_promotion(self):
        self.states['notes-postgres'] = 't|on|0/20|0/10|16'
        with self.assertRaisesRegex(todo_dr.DrError, 'notes.*unreplayed'):
            self.tool().promote('primary is fenced', 'standby')
        self.assertFalse(any('pg_ctl' in command for command in self.commands))
        self.assertFalse(self.journal.exists())

    def test_last_app_missing_lsn_or_wrong_role_prevents_every_promotion(self):
        for state in ('t|on|||0', 'f|off|||0', 't|off|0/10|0/10|0'):
            self.states['notes-postgres'] = state
            with self.assertRaises(todo_dr.DrError):
                self.tool().promote('primary is fenced', 'standby')
        self.assertFalse(any('pg_ctl' in command for command in self.commands))
        self.assertFalse(self.journal.exists())

    def test_every_primary_port_must_be_unreachable(self):
        with self.assertRaisesRegex(todo_dr.DrError, '5433'):
            self.tool(reachable_port=5433).promote('primary is fenced', 'standby')
        self.assertEqual(self.endpoints, [5432, 5433])
        self.assertFalse(any('pg_ctl' in command for command in self.commands))

    def test_complete_group_is_checked_before_first_promotion(self):
        states = self.tool().promote('primary is fenced', 'standby')
        self.assertEqual(set(states), {'todo', 'notes'})
        self.assertTrue(all(not state.in_recovery for state in states.values()))
        first = next(i for i, command in enumerate(self.commands) if 'pg_ctl' in command)
        for app in self.apps:
            self.assertTrue(any('psql' in command and app.resource('postgres') in command
                                for command in self.commands[:first]))
        decision = json.loads(self.journal.read_text())
        self.assertEqual(decision['state'], 'complete')
        self.assertEqual(decision['completed'], ['todo', 'notes'])
        self.assertEqual(self.journal.stat().st_mode & 0o777, 0o600)

    def test_partial_failure_is_recorded_and_blind_retry_is_refused(self):
        self.failed_promotion = 'notes-postgres'
        tool = self.tool()
        with self.assertRaisesRegex(todo_dr.DrError, 'injected'):
            tool.promote('primary is fenced', 'standby')
        decision = json.loads(self.journal.read_text())
        self.assertEqual(decision['state'], 'failed')
        self.assertEqual(decision['completed'], ['todo'])
        count = len(self.commands)
        with self.assertRaisesRegex(todo_dr.DrError, 'decision already exists'):
            tool.promote('primary is fenced', 'standby')
        self.assertEqual(len(self.commands), count)

    def test_explicit_complete_group_is_required(self):
        for names in ((), ('todo',), ('notes', 'todo')):
            with self.assertRaisesRegex(todo_dr.DrError, 'complete ordered'):
                todo_dr.TodoDr(todo_dr.Config('primary', '192.0.2.50', 'standby', 30, names))

    def test_failed_durable_decision_prevents_every_promotion(self):
        tool = self.tool()
        with mock.patch.object(tool, '_record', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                tool.promote('primary is fenced', 'standby')
        self.assertFalse(any('pg_ctl' in command for command in self.commands))

    def test_last_app_service_or_health_failure_prevents_every_promotion(self):
        for failure in ('service', 'health'):
            tool = self.tool()
            original = self.runner

            def failed(arguments, timeout=None):
                if failure == 'service' and 'notes-postgres.service' in arguments:
                    return completed('inactive')
                if failure == 'health' and arguments[:2] == ['podman', 'inspect'] and 'notes-postgres' in arguments:
                    return completed('starting')
                return original(arguments, timeout)

            tool.runner = failed
            with self.assertRaises(todo_dr.DrError):
                tool.promote('primary is fenced', 'standby')
            self.assertFalse(any('pg_ctl' in command for command in self.commands))

    def test_concurrent_promotion_cannot_enter_preflight(self):
        tool = self.tool()
        with tool._promotion_lock(), self.assertRaisesRegex(todo_dr.DrError, 'holds the lock'):
            self.tool().promote('primary is fenced', 'standby')
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
