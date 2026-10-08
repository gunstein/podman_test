import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "deploy/dr/scripts" / "app_dr.py"
# On a host the script finds both packages in lib next to it; here they come
# from the checkout, as PYTHONPATH=deploy/installer:deploy/dr would give them.
sys.path[:0] = [str(Path(__file__).parents[1] / "deploy/installer"), str(Path(__file__).parents[1] / "deploy/dr")]
SPEC = importlib.util.spec_from_file_location("app_dr", SCRIPT)
assert SPEC and SPEC.loader
app_dr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_dr)

from app_installer.commands import CommandError  # noqa: E402

from tests.fake_commands import route_commands  # noqa: E402


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def container(arguments):
    """The container a podman exec command runs in; sql() adds --interactive before it."""
    return arguments[3] if arguments[2] == "--interactive" else arguments[2]


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


class StandbyGroupTests(unittest.TestCase):
    def setUp(self):
        registry = mock.patch.object(app_dr.apps, 'REPLICATED_DATABASES', (app_dr.apps.APPS[0].database,))
        registry.start()
        self.addCleanup(registry.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.journal = Path(self.temporary.name) / 'promotion.json'

    def config(self):
        return app_dr.Config("todo-primary", "192.0.2.10", "todo-standby", 30)

    def tool(self, outputs, reachable=False):
        runner = FakeRunner(outputs)
        route_commands(self, runner)
        tool = app_dr.StandbyGroup(
            self.config(),
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
            self.assertEqual(app_dr.load_config(path).rpo_target_seconds, 30)

    def test_status_reports_normal_standby(self):
        tool, _runner = self.tool(["t|on|0/10|0/10"], reachable=True)
        output = "\n".join(tool.status_lines())
        self.assertIn("Database role: standby", output)
        self.assertIn("Writable: no", output)
        self.assertIn("Primary endpoint 192.0.2.10:5432: reachable", output)

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_preflight_accepts_fenced_caught_up_standby(self, _hostname):
        tool, _runner = self.tool(["t|on|0/10|0/10"])
        status = tool.preflight("todo-primary is fenced")
        self.assertTrue(status["todo"].in_recovery)

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_preflight_rejects_reachable_primary(self, _hostname):
        tool, _runner = self.tool(["t|on|0/10|0/10"], reachable=True)
        with self.assertRaisesRegex(app_dr.DrError, "still answers"):
            tool.preflight("todo-primary is fenced")

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_preflight_rejects_missing_lsn(self, _hostname):
        tool, _runner = self.tool(["t|on||"])
        with self.assertRaisesRegex(RuntimeError, "LSN is unavailable"):
            tool.preflight("todo-primary is fenced")

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_preflight_rejects_local_apply_lag(self, _hostname):
        tool, _runner = self.tool(["t|on|0/20|0/10"])
        with self.assertRaisesRegex(RuntimeError, "unreplayed local WAL"):
            tool.preflight("todo-primary is fenced")

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_preflight_after_a_walreceiver_restart(self, _hostname):
        # Receive behind replay (the exact values from an acceptance run) means
        # nothing is left to replay; equal passes; one byte ahead still refuses.
        for receive, replay, lag in (("0/3000000", "0/3000060", 0), ("0/3000060", "0/3000060", 0),
                                     ("0/3000061", "0/3000060", 1)):
            with self.subTest(receive=receive, replay=replay):
                tool, _runner = self.tool([f"t|on|{receive}|{replay}"])
                if lag:
                    with self.assertRaisesRegex(RuntimeError, "unreplayed local WAL: 1 bytes"):
                        tool.preflight("todo-primary is fenced")
                else:
                    self.assertEqual(tool.preflight("todo-primary is fenced")["todo"].apply_lag_bytes, 0)

    def test_status_never_reports_a_negative_lag(self):
        tool, _runner = self.tool(["t|on|0/3000000|0/3000060"])
        output = "\n".join(tool.status_lines())
        self.assertIn("Local apply lag: 0 bytes (receive restarted at the WAL segment start", output)
        self.assertNotIn("-96", output)
        tool, _runner = self.tool(["t|on|0/10|0/10"])
        self.assertIn("Local apply lag: 0 bytes\n", "\n".join(tool.status_lines()))

    @mock.patch.object(socket, "gethostname", return_value="wrong-host")
    def test_preflight_rejects_wrong_host(self, _hostname):
        tool, _runner = self.tool([])
        with self.assertRaisesRegex(app_dr.DrError, "configured standby host"):
            tool.preflight("todo-primary is fenced")

    def test_preflight_requires_exact_fencing_confirmation(self):
        tool, _runner = self.tool([])
        with self.assertRaisesRegex(app_dr.DrError, "must be exactly"):
            tool.preflight("yes")

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_promote_rechecks_and_verifies_writable_database(self, _hostname):
        tool, runner = self.tool(
            ["t|on|0/10|0/10", "f|off||"], reachable=False
        )
        status = tool.promote("todo-primary is fenced", "todo-standby")
        self.assertFalse(status["todo"].in_recovery)
        self.assertEqual(json.loads(self.journal.read_text())["state"], "complete")
        flattened = [item for command in runner.commands for item in command]
        self.assertIn("pg_ctl", flattened)

    @mock.patch.object(socket, "gethostname", return_value="todo-standby")
    def test_promote_requires_exact_standby_confirmation(self, _hostname):
        tool, runner = self.tool(["t|on|0/10|0/10"])
        with self.assertRaisesRegex(app_dr.DrError, "standby hostname"):
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
            self.assertEqual(app_dr.load_config(path).rpo_target_seconds, 45)

    def test_status_labels_rpo_target_as_informational(self):
        tool, _runner = self.tool(["t|on|0/10|0/10"], reachable=True)
        output = "\n".join(tool.status_lines())
        self.assertIn("Configured RPO target (informational)", output)

    def test_control_command_timeout_is_actionable(self):
        def timeout_runner(arguments, timeout=None):
            raise subprocess.TimeoutExpired(arguments, timeout)

        route_commands(self, timeout_runner)
        tool = app_dr.StandbyGroup(self.config())
        with self.assertRaisesRegex(RuntimeError, "systemd status check timed out after 120 seconds"):
            tool.service_status()

class GroupPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.journal = Path(self.temporary.name) / 'promotion.json'
        self.apps = (app_dr.apps.APPS[0].database, app_dr.apps.APPS[1].database)
        self.registry = mock.patch.object(app_dr.apps, 'REPLICATED_DATABASES', self.apps)
        self.registry.start()
        self.addCleanup(self.registry.stop)
        self.hostname = mock.patch.object(socket, 'gethostname', return_value='standby')
        self.hostname.start()
        self.addCleanup(self.hostname.stop)
        self.states = {app.container: 't|on|0/10|0/10' for app in self.apps}
        self.commands = []
        self.endpoints = []
        self.failed_promotion = None
        self.active_runner = self.runner
        route_commands(self, lambda arguments, timeout: self.active_runner(arguments, timeout))

    def runner(self, arguments, timeout=None):
        self.commands.append(list(arguments))
        if arguments[:3] == ['systemctl', '--user', 'is-active']:
            return completed('active')
        if arguments[:3] == ['podman', 'inspect', '--format']:
            return completed('healthy')
        if 'psql' in arguments:
            return completed(self.states[container(arguments)])
        if 'pg_ctl' in arguments:
            # A durable decision naming BOTH apps must precede the first promotion.
            decision = json.loads(self.journal.read_text())
            self.assertEqual(decision['applications'], ['todo', 'notes'])
            self.assertEqual(decision['state'], 'promoting')
            if arguments[2] == self.failed_promotion:
                return completed(stderr='injected promotion failure', returncode=1)
            self.states[arguments[2]] = 'f|off||'
            return completed()
        raise AssertionError(arguments)

    def tool(self, reachable_port=None):
        def connect(address, port, timeout):
            self.endpoints.append(port)
            return port == reachable_port
        return app_dr.StandbyGroup(app_dr.Config('primary', '192.0.2.50', 'standby', 30, ('todo', 'notes')),
                              connector=connect, journal_path=self.journal)

    def test_last_app_lag_prevents_every_promotion(self):
        self.states['notes-postgres'] = 't|on|0/20|0/10'
        with self.assertRaisesRegex(RuntimeError, 'notes.*unreplayed'):
            self.tool().promote('primary is fenced', 'standby')
        self.assertFalse(any('pg_ctl' in command for command in self.commands))
        self.assertFalse(self.journal.exists())

    def test_last_app_missing_lsn_or_wrong_role_prevents_every_promotion(self):
        for state in ('t|on||', 'f|off||', 't|off|0/10|0/10'):
            self.states['notes-postgres'] = state
            with self.assertRaisesRegex(RuntimeError, 'notes: '):
                self.tool().promote('primary is fenced', 'standby')
        self.assertFalse(any('pg_ctl' in command for command in self.commands))
        self.assertFalse(self.journal.exists())

    def test_every_primary_port_must_be_unreachable(self):
        with self.assertRaisesRegex(app_dr.DrError, '5433'):
            self.tool(reachable_port=5433).promote('primary is fenced', 'standby')
        self.assertEqual(self.endpoints, [5432, 5433])
        self.assertFalse(any('pg_ctl' in command for command in self.commands))

    def test_complete_group_is_checked_before_first_promotion(self):
        states = self.tool().promote('primary is fenced', 'standby')
        self.assertEqual(set(states), {'todo', 'notes'})
        self.assertTrue(all(not state.in_recovery for state in states.values()))
        first = next(i for i, command in enumerate(self.commands) if 'pg_ctl' in command)
        for app in self.apps:
            self.assertTrue(any('psql' in command and app.container in command
                                for command in self.commands[:first]))
        decision = json.loads(self.journal.read_text())
        self.assertEqual(decision['state'], 'complete')
        self.assertEqual(decision['completed'], ['todo', 'notes'])
        self.assertEqual(self.journal.stat().st_mode & 0o777, 0o600)

    def test_partial_failure_is_recorded_and_blind_retry_is_refused(self):
        self.failed_promotion = 'notes-postgres'
        tool = self.tool()
        with self.assertRaisesRegex(RuntimeError, 'notes: PostgreSQL promotion failed .*injected'):
            tool.promote('primary is fenced', 'standby')
        decision = json.loads(self.journal.read_text())
        self.assertEqual(decision['state'], 'failed')
        self.assertEqual(decision['completed'], ['todo'])
        count = len(self.commands)
        with self.assertRaisesRegex(app_dr.DrError, 'decision already exists'):
            tool.promote('primary is fenced', 'standby')
        self.assertEqual(len(self.commands), count)

    def test_explicit_complete_group_is_required(self):
        for names in ((), ('todo',), ('notes', 'todo')):
            with self.assertRaisesRegex(app_dr.DrError, 'complete ordered'):
                app_dr.StandbyGroup(app_dr.Config('primary', '192.0.2.50', 'standby', 30, names))

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

            def failed(arguments, timeout=None, failure=failure, original=original):
                if failure == 'service' and 'notes-postgres.service' in arguments:
                    return completed('inactive')
                if failure == 'health' and arguments[:2] == ['podman', 'inspect'] and 'notes-postgres' in arguments:
                    return completed('starting')
                return original(arguments, timeout)

            self.active_runner = failed
            with self.assertRaises(app_dr.DrError):
                tool.promote('primary is fenced', 'standby')
            self.assertFalse(any('pg_ctl' in command for command in self.commands))

    def test_concurrent_promotion_cannot_enter_preflight(self):
        tool = self.tool()
        with tool._promotion_lock(), self.assertRaisesRegex(app_dr.DrError, 'holds the lock'):
            self.tool().promote('primary is fenced', 'standby')
        self.assertEqual(self.commands, [])


class CheckHost:
    """Answers the check's SQL per database from a small description of each one.

    How long the certificates last comes from the description too:
    certificate_days per database (a primary's replication certificate) and
    ca_days for the replication CA. The real reading of a certificate is
    tested with openssl in deploy/dr/tests/test_replication_tls.py.
    """

    def __init__(self, test, ca_days=3000, **databases):
        healthy = dict(role="primary", streams="1", slots="todo_standby|t|reserved|", archive="on",
                       archiver="f|off|on|000000010000000000000003|0|healthy", receiver="streaming",
                       certificate_days=400)
        self.databases = {f"{name}-postgres": {**healthy, **changes} for name, changes in databases.items()}
        route_commands(test, self)
        tls = app_dr.replication_tls
        for target, name, days in ((tls, "server_days_left", self.certificate_days),
                                   (tls, "ca_days_left", lambda: ca_days),
                                   # nginx's certificate: a pair in local mode unless a test says otherwise.
                                   (app_dr.nginx_tls, "readiness", lambda: ([], []))):
            patcher = mock.patch.object(target, name, side_effect=days)
            patcher.start()
            test.addCleanup(patcher.stop)

    def certificate_days(self, database):
        days = self.databases[database.container]["certificate_days"]
        if isinstance(days, Exception):
            raise days
        return days

    def __call__(self, arguments, timeout=None):
        database, statement = self.databases[container(arguments)], arguments[-1]
        if database.get("down"):
            return completed(stderr="Error: no container with name or ID", returncode=125)
        if "pg_is_in_recovery(), current_setting('transaction_read_only'), COALESCE(pg_last" in statement:
            return completed("f|off|0/30|0/30" if database["role"] == "primary" else "t|on|0/30|0/30")
        if "FROM pg_stat_replication" in statement:
            return completed(database["streams"])
        if "FROM pg_replication_slots" in statement:
            return completed(database["slots"])
        if "pg_stat_wal_receiver" in statement:
            return completed(database["receiver"])
        if statement == "SELECT current_setting('archive_mode');":
            return completed(database["archive"])
        if "FROM pg_stat_archiver" in statement:
            return completed(database["archiver"])
        raise AssertionError(statement)


class CheckTests(unittest.TestCase):
    """app_dr.py check: one run names every problem for the host's role."""

    DISK = (100 * 2**30, 50 * 2**30, 50 * 2**30)

    def check(self, disk=DISK, ca_days=3000, **databases):
        CheckHost(self, ca_days, **{name: databases.get(name, {}) for name in ("todo", "notes", "keycloak")})
        return app_dr.check(disk=disk)

    def test_a_healthy_primary_and_a_healthy_standby_pass(self):
        lines, problems = self.check()
        self.assertEqual(problems, [])
        self.assertIn("todo: primary, 1 standby streaming over TLS, WAL archive healthy", lines)
        self.assertIn("Disk: 50% free (51200 MiB)", lines)
        standby = {"role": "standby"}
        lines, problems = self.check(todo=standby, notes=standby, keycloak=standby)
        self.assertEqual(problems, [])
        self.assertIn("keycloak: standby, receiving WAL from the primary", lines)

    def test_each_primary_certificate_and_the_ca_are_reported_with_their_days(self):
        lines, problems = self.check()
        self.assertEqual(problems, [])
        self.assertIn("todo: replication certificate: valid 400 more days", lines)
        self.assertIn("keycloak: replication certificate: valid 400 more days", lines)
        self.assertIn("Replication CA: valid 3000 more days", lines)
        # A standby uses the primary's certificate: only the CA is checked there.
        standby = {"role": "standby"}
        lines, problems = self.check(todo=standby, notes=standby, keycloak=standby)
        self.assertEqual(problems, [])
        self.assertFalse([line for line in lines if "replication certificate" in line])
        self.assertIn("Replication CA: valid 3000 more days", lines)

    def test_a_certificate_the_renewal_did_not_replace_fails_the_check(self):
        _lines, problems = self.check(notes={"certificate_days": 24})
        self.assertEqual(problems, ["notes: replication certificate expires in 24 days; "
                                    "todo-replication-tls.timer has not renewed it"])
        # 25 days left is still fine: the nightly renewal starts at 30.
        _lines, problems = self.check(notes={"certificate_days": 25})
        self.assertEqual(problems, [])

    def test_an_expired_certificate_is_named_next_to_the_stream_it_broke(self):
        _lines, problems = self.check(todo={"streams": "0", "certificate_days": -3})
        self.assertEqual(problems, ["todo: no standby streams from this primary over TLS",
                                    "todo: replication certificate expires in -3 days; "
                                    "todo-replication-tls.timer has not renewed it"])

    def test_a_primary_without_a_readable_certificate_fails(self):
        missing = RuntimeError("todo: the primary has no replication certificate")
        _lines, problems = self.check(todo={"certificate_days": missing})
        self.assertEqual(problems, ["todo: replication certificate: cannot read its expiry "
                                    "(todo: the primary has no replication certificate)"])

    def test_a_ca_that_is_about_to_expire_fails_on_either_host(self):
        standby = {"role": "standby"}
        for databases in ({}, {"todo": standby, "notes": standby, "keycloak": standby}):
            with self.subTest(databases=databases):
                _lines, problems = self.check(ca_days=179, **databases)
                self.assertEqual(problems, ["Replication CA expires in 179 days; "
                                            "replace it by hand before then (backlog U2)"])

    def test_archiving_off_is_reported_not_failed(self):
        lines, problems = self.check(todo={"archive": "off"})
        self.assertEqual(problems, [])
        self.assertIn("todo: primary, 1 standby streaming over TLS, WAL archiving off", lines)

    def test_each_problem_is_found_and_every_database_is_checked(self):
        cases = (
            ({"streams": "0"}, "todo: no standby streams from this primary over TLS"),
            ({"slots": "todo_standby|f|reserved|"}, "todo: replication slot todo_standby is inactive"),
            ({"slots": "todo_standby|t|lost|wal_removed"}, "losing WAL or invalidated"),
            ({"archiver": "f|off|on|000000010000000000000003|2|failed"}, "WAL archiving has not recovered"),
            ({"down": True}, "todo: "),
        )
        for changes, message in cases:
            with self.subTest(message=message):
                _lines, problems = self.check(todo=changes, keycloak={"streams": "0"})
                self.assertEqual(len(problems), 2, problems)
                self.assertIn(message, problems[0])
                self.assertIn("keycloak: no standby streams", problems[1])

    def test_a_standby_that_receives_nothing_fails(self):
        standby = {"role": "standby"}
        _lines, problems = self.check(todo={"role": "standby", "receiver": "waiting"}, notes=standby,
                                      keycloak={"role": "standby", "receiver": ""})
        self.assertEqual(problems, [
            "todo: the standby does not receive WAL from the primary (waiting)",
            "keycloak: the standby does not receive WAL from the primary (no WAL receiver)"])

    def test_a_split_group_fails(self):
        _lines, problems = self.check(notes={"role": "standby"})
        self.assertEqual(problems, ["the group is split: todo primary, notes standby, keycloak primary"])

    def test_a_nearly_full_disk_fails(self):
        _lines, problems = self.check(disk=(100 * 2**30, 95 * 2**30, 5 * 2**30))
        self.assertEqual(problems, ["only 5% of the disk is free (5120 MiB); the check wants 10%"])

    def test_the_command_prints_what_is_fine_and_exits_1_on_a_problem(self):
        CheckHost(self, todo={"streams": "0"}, notes={}, keycloak={})
        with mock.patch.object(app_dr.shutil, "disk_usage", return_value=self.DISK), \
                mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr") as stderr:
            self.assertEqual(app_dr.main(["check"]), 1)
        printed = "".join(call.args[0] for call in stdout.write.call_args_list)
        errors = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("notes: primary", printed)
        self.assertIn("ERROR: todo: no standby streams", errors)

    def test_without_dr_settings_the_check_still_reports_replication_and_fails(self):
        CheckHost(self, todo={}, notes={}, keycloak={})
        with mock.patch.object(app_dr.shutil, "disk_usage", return_value=self.DISK), \
                mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr") as stderr:
            self.assertEqual(app_dr.main(["--config", "/nonexistent/todo-dr.json", "check"]), 1)
        self.assertIn("todo: primary", "".join(call.args[0] for call in stdout.write.call_args_list))
        self.assertIn("ERROR: Cannot read valid DR configuration from /nonexistent/todo-dr.json",
                      "".join(call.args[0] for call in stderr.write.call_args_list))

    def test_a_provided_pair_reports_this_hosts_nginx_certificate(self):
        CheckHost(self, todo={}, notes={}, keycloak={})
        app_dr.nginx_tls.readiness.side_effect = lambda: (
            [], ["nginx could not start here with your CA's certificate: server.key is missing"])
        with mock.patch.object(app_dr.shutil, "disk_usage", return_value=self.DISK), \
                mock.patch("sys.stdout"), mock.patch("sys.stderr") as stderr:
            self.assertEqual(app_dr.main(["--config", "/nonexistent/todo-dr.json", "check"]), 1)
        self.assertIn("ERROR: nginx could not start here with your CA's certificate: server.key is missing",
                      "".join(call.args[0] for call in stderr.write.call_args_list))
        # A failure to look is a problem too, never a crash of the whole check.
        app_dr.nginx_tls.readiness.side_effect = RuntimeError("podman image inspect failed")
        with mock.patch.object(app_dr.shutil, "disk_usage", return_value=self.DISK), \
                mock.patch("sys.stdout"), mock.patch("sys.stderr") as stderr:
            self.assertEqual(app_dr.main(["--config", "/nonexistent/todo-dr.json", "check"]), 1)
        self.assertIn("ERROR: cannot check the nginx certificate: podman image inspect failed",
                      "".join(call.args[0] for call in stderr.write.call_args_list))

    def test_a_ready_host_passes_the_whole_check(self):
        CheckHost(self, todo={}, notes={}, keycloak={})
        with tempfile.TemporaryDirectory() as directory:
            bundle = ReadinessTests.bundle(Path(directory))
            config = Path(directory) / "todo-dr.json"
            app_dr.write_config(config, "todo-primary", "192.0.2.10", "todo-standby", 30, "a" * 40, str(bundle))
            with mock.patch.object(app_dr.shutil, "disk_usage", return_value=self.DISK), \
                    mock.patch.object(app_dr, "secret_exists", return_value=True), \
                    mock.patch("sys.stdout") as stdout:
                self.assertEqual(app_dr.main(["--config", str(config), "check"]), 0)
        self.assertIn("Ready to take over: offline bundle aaaaaaaaaaaa with 2 image archives",
                      "".join(call.args[0] for call in stdout.write.call_args_list))


class RenewTlsTests(unittest.TestCase):
    """app_dr.py renew-tls: the nightly renewal renews what is due on the primary, and nothing on a standby."""

    def setUp(self):
        self.roles = {"todo": "f", "notes": "f", "keycloak": "f"}
        self.renewed, self.failing = [], {}

        def query(database, _statement):
            return f"{self.roles[database.name]}|off|0/30|0/30"

        def renew(database):
            if database.name in self.failing:
                raise self.failing[database.name]
            return database.name in self.renewed

        for target, name, fake in ((app_dr.StandbyGroup, "_query", query),
                                   (app_dr.replication_tls, "renew", renew),
                                   (app_dr.replication_tls, "server_days_left", lambda database: 824),
                                   (app_dr.nginx_tls, "renew", lambda: ([], []))):
            patcher = mock.patch.object(target, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_due_certificates_are_renewed_and_the_others_kept(self):
        self.renewed = ["notes"]
        lines, problems = app_dr.renew_tls()
        self.assertEqual(problems, [])
        self.assertEqual(lines, ["todo: replication certificate kept, valid 824 more days",
                                 "notes: new replication certificate, valid 824 more days",
                                 "keycloak: replication certificate kept, valid 824 more days"])

    def test_a_standby_renews_nothing(self):
        self.roles = dict.fromkeys(self.roles, "t")
        lines, problems = app_dr.renew_tls()
        self.assertEqual(problems, [])
        self.assertEqual(lines, [f"{name}: standby, nothing to renew" for name in ("todo", "notes", "keycloak")])
        app_dr.replication_tls.renew.assert_not_called()

    def test_one_failure_does_not_stop_the_others_and_the_command_exits_1(self):
        self.renewed = ["keycloak"]
        self.failing = {"todo": CommandError("openssl x509 failed (exit 1)")}
        with mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr") as stderr:
            self.assertEqual(app_dr.main(["renew-tls"]), 1)
        printed = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn("keycloak: new replication certificate", printed)
        self.assertIn("notes: replication certificate kept", printed)
        self.assertIn("ERROR: openssl x509 failed (exit 1)",
                      "".join(call.args[0] for call in stderr.write.call_args_list))

    def test_the_command_exits_0_when_everything_is_fine(self):
        with mock.patch("sys.stdout"):
            self.assertEqual(app_dr.main(["renew-tls"]), 0)

    def test_the_nightly_run_also_prepares_nginxs_next_request(self):
        app_dr.nginx_tls.renew.side_effect = lambda: (
            ["nginx certificate (provided mode): valid 50 more days", "A request for the next certificate is ready"],
            [])
        with mock.patch("sys.stdout") as stdout:
            self.assertEqual(app_dr.main(["renew-tls"]), 0)
        printed = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn("todo: replication certificate kept", printed)
        self.assertIn("A request for the next certificate is ready", printed)


class ReadinessTests(unittest.TestCase):
    """app_dr.readiness: could this host take over now? Files and Podman secrets only."""

    REVISION = "a" * 40

    @staticmethod
    def bundle(directory, revision="a" * 40, archives=("images/postgres-17.11.tar", "images/keycloak-m12.tar")):
        bundle = directory / "todo-offline-m12"
        (bundle / "images").mkdir(parents=True)
        (bundle / "VERSION").write_text(f"package=todo-offline-m12\nsource_revision={revision}\nsource_state=clean\n")
        (bundle / "SHA256SUMS").write_text("".join(f"{'0' * 64}  ./{name}\n" for name in archives)
                                           + f"{'0' * 64}  ./install.sh\n")
        for name in archives:
            (bundle / name).write_bytes(b"archive")
        return bundle

    def readiness(self, bundle, revision=REVISION, missing=()):
        config = app_dr.Config("todo-primary", "192.0.2.10", "todo-standby", 30, (), revision, str(bundle))
        return app_dr.readiness(config, exists=lambda name: name not in missing)

    def test_a_host_with_the_bundle_and_every_secret_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            lines, problems = self.readiness(self.bundle(Path(directory)))
        self.assertEqual(problems, [])
        names = app_dr.transfer.transfer_names()
        self.assertIn("replication-ca-key", names)
        self.assertEqual(lines, [f"Ready to take over: offline bundle aaaaaaaaaaaa with 2 image archives, "
                                 f"all {len(names)} DR secrets"])

    def test_every_missing_piece_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.bundle(Path(directory), revision="b" * 40)
            (bundle / "images/keycloak-m12.tar").unlink()
            lines, problems = self.readiness(bundle, missing=("notes-replicator-password", "replication-ca-cert"))
        self.assertEqual(lines, [])
        self.assertEqual(len(problems), 3, problems)
        self.assertIn(f"is revision {'b' * 40}, but the DR tool was installed from {self.REVISION}", problems[0])
        self.assertIn("lacks image archives: ./images/keycloak-m12.tar", problems[1])
        self.assertEqual(problems[2], "DR secrets missing on this host: notes-replicator-password, "
                                      "replication-ca-cert")

    def test_no_bundle_or_no_settings_for_it_is_a_problem(self):
        with tempfile.TemporaryDirectory() as directory:
            _lines, problems = self.readiness(Path(directory) / "missing")
            self.assertIn("no complete offline bundle at", problems[0])
            config = app_dr.Config("todo-primary", "192.0.2.10", "todo-standby", 30)
            _lines, problems = app_dr.readiness(config, exists=lambda name: True)
            self.assertEqual(problems, ["the DR settings name no offline bundle: run app-ops install-dr-tool again"])

    def test_a_checkout_without_a_revision_checks_everything_else(self):
        with tempfile.TemporaryDirectory() as directory:
            _lines, problems = self.readiness(self.bundle(Path(directory), revision="c" * 40), revision="")
        self.assertEqual(problems, [])


class ConfigureTests(unittest.TestCase):
    def configure(self, config, *extra):
        return app_dr.main(['--config', str(config), 'configure', '--primary-name', 'todo-primary',
                             '--primary-address', '192.0.2.10', '--standby-name', 'todo-standby', *extra])

    def test_writes_a_private_complete_group_config_that_the_tool_reads_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "todo" / "todo-dr.json"
            with mock.patch("sys.stdout") as stdout:
                self.assertEqual(self.configure(config), 0)
            self.assertIn('"changed": true', "".join(call.args[0] for call in stdout.write.call_args_list))
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(config.parent.stat().st_mode & 0o777, 0o700)
            loaded = app_dr.load_config(config)
            self.assertEqual(loaded.applications, tuple(d.name for d in app_dr.apps.REPLICATED_DATABASES))
            self.assertEqual((loaded.primary_name, loaded.primary_address, loaded.standby_name,
                              loaded.rpo_target_seconds), ("todo-primary", "192.0.2.10", "todo-standby", 30))
            app_dr.StandbyGroup(loaded)

    def test_repeat_is_unchanged_and_existing_ansible_written_config_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "todo-dr.json"
            # Exactly what the former Ansible to_json task wrote (ansible-core 2.14 and 2.20).
            config.write_text('{"applications": ["todo", "notes", "keycloak"], "primary_name": "todo-primary", '
                              '"primary_address": "192.0.2.10", "standby_name": "todo-standby", '
                              '"rpo_target_seconds": 30}')
            config.chmod(0o600)
            self.assertFalse(app_dr.write_config(config, "todo-primary", "192.0.2.10", "todo-standby", 30))
            self.assertTrue(app_dr.write_config(config, "todo-primary", "192.0.2.10", "todo-standby", 60))
            self.assertEqual(app_dr.load_config(config).rpo_target_seconds, 60)
            self.assertTrue(app_dr.write_config(config, "todo-primary", "192.0.2.10", "todo-standby", 60,
                                                "a" * 40, "/home/u/todo-offline-m12"))
            loaded = app_dr.load_config(config)
            self.assertEqual((loaded.revision, loaded.bundle), ("a" * 40, "/home/u/todo-offline-m12"))

    def test_invalid_values_are_refused_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "todo-dr.json"
            for arguments in (("primary.example", "todo-standby", 30), ("192.0.2.10", "", 30),
                              ("192.0.2.10", "todo-standby", 0)):
                with self.subTest(arguments=arguments), self.assertRaises(app_dr.DrError):
                    app_dr.write_config(config, "todo-primary", *arguments)
            self.assertFalse(config.exists())
            with mock.patch("sys.stderr"):
                self.assertEqual(self.configure(config, '--rpo-target-seconds', '-5'), 1)


if __name__ == "__main__":
    unittest.main()
