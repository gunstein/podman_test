import importlib.util
import io
import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "deploy/dr/scripts" / "app_backup.py"
# On a host the script finds both packages in lib next to it; here they come
# from the checkout, as PYTHONPATH=deploy/installer:deploy/dr would give them.
sys.path[:0] = [str(Path(__file__).parents[1] / "deploy/installer"), str(Path(__file__).parents[1] / "deploy/dr")]
SPEC = importlib.util.spec_from_file_location("app_backup", SCRIPT)
assert SPEC and SPEC.loader
app_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_backup)

from tests.fake_commands import route_commands  # noqa: E402


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def backup(test, runner, **kwargs):
    """A DatabaseBackup whose commands go to runner for the rest of test."""
    route_commands(test, runner)
    return app_backup.DatabaseBackup(**kwargs)


class FakeRunner:
    def __init__(
        self, recovery="f|off", containers=None, volumes=None,
        archive_status="on|000000010000000000000001||1h|1|0", backups=(),
    ):
        self.backups = list(backups)
        self.recovery = recovery
        self.containers = set(containers or ())
        self.volumes = set(volumes or ())
        self.archive_status = archive_status
        self.commands = []

    def __call__(self, arguments, timeout=None):
        command = list(arguments)
        self.commands.append(command)

        if command[:3] == ["podman", "container", "exists"]:
            return completed(returncode=0 if command[3] in self.containers else 1)
        if command[:3] == ["podman", "volume", "exists"]:
            return completed(returncode=0 if command[3] in self.volumes else 1)
        if 'cat "$1"; ls "$2"' in command:  # backups.listing: LATEST, then every backup
            return completed("\n".join([max(self.backups, default="none"), *self.backups]) + "\n")
        if "psql" in command:
            sql = command[-1]
            if "pg_is_wal_replay_paused" in sql:
                return completed("t|t\n")
            if "pg_is_in_recovery" in sql:
                return completed(self.recovery + "\n")
            if "FROM pg_stat_archiver" in sql:
                return completed(self.archive_status + "\n")
            if "pg_create_restore_point" in sql:
                return completed("0/5000100\n")
            if "pg_walfile_name" in sql:
                return completed("000000010000000000000001\n")
            if "pg_switch_wal" in sql:
                return completed("0/6000000\n")
        return completed("ok\n")


class FakeTime:
    """A deadline clock that moves only when the code under test sleeps."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def fake_time():
    time = FakeTime()
    return {"sleeper": time.sleep, "monotonic": time.monotonic}


class DatabaseBackupTests(unittest.TestCase):
    def tool(self, runner):
        return backup(self, 
            runner,
            clock=lambda: datetime(2026, 8, 29, 12, 34, 56, tzinfo=timezone.utc),
            **fake_time(),
        )

    def test_status_reports_writable_archive(self):
        output = "\n".join(self.tool(FakeRunner()).status_lines())
        self.assertIn("Database writable: yes", output)
        self.assertIn("Archive mode: on", output)
        self.assertIn("Archive timeout: 1h", output)
        self.assertIn("Failed archive attempts: 0", output)

    def test_create_backup_runs_basebackup_and_verification(self):
        runner = FakeRunner()
        name = self.tool(runner).create_backup()
        self.assertEqual(name, "base-20260829T123456Z")
        flattened = [item for command in runner.commands for item in command]
        self.assertIn("pg_basebackup", flattened)
        self.assertIn("pg_verifybackup", flattened)
        self.assertIn("--manifest-checksums=SHA256", flattened)

    def test_create_backup_rejects_read_only_database(self):
        with self.assertRaisesRegex(app_backup.BackupError, "not a writable"):
            self.tool(FakeRunner(recovery="t|on")).create_backup()

    def test_restore_point_is_archived(self):
        runner = FakeRunner()
        lsn = self.tool(runner).create_restore_point("before_delete")
        self.assertEqual(lsn, "0/5000100")
        flattened = [item for command in runner.commands for item in command]
        self.assertIn("SELECT pg_create_restore_point('before_delete');", flattened)
        self.assertNotIn(":'restore_point'", flattened)
        self.assertIn("pg_switch_wal", " ".join(flattened))

    def test_restore_point_accepts_success_after_historical_archive_failure(self):
        runner = FakeRunner(
            archive_status=(
                "on|000000010000000000000001|000000010000000000000000|1h|2|1"
            )
        )
        self.tool(runner).create_restore_point("after_old_failure")

    def test_restore_rejects_existing_disposable_state_without_replace(self):
        restore_container = app_backup.apps.APPS[0].names.resource("postgres-restore")
        runner = FakeRunner(containers={restore_container})
        with self.assertRaisesRegex(app_backup.BackupError, "--replace"):
            self.tool(runner).restore(
                "base-20260829T123456Z", "before_delete", replace=False
            )

    def test_restore_uses_only_fixed_disposable_targets(self):
        runner = FakeRunner()
        tool = self.tool(runner)
        tool.restore("base-20260829T123456Z", "before_delete", replace=False)
        flattened = [item for command in runner.commands for item in command]
        self.assertIn(tool.restore_container, flattened)
        self.assertIn(tool.restore_volume, flattened)
        self.assertNotIn("todo-postgres-data", flattened)

    def test_cleanup_requires_exact_confirmation(self):
        with self.assertRaisesRegex(app_backup.BackupError, "exactly"):
            self.tool(FakeRunner()).cleanup_restore("yes")

    def test_rejects_unsafe_backup_and_restore_point_names(self):
        with self.assertRaises(app_backup.BackupError):
            app_backup.DatabaseBackup._validate_backup_name("../../data")
        with self.assertRaises(app_backup.BackupError):
            app_backup.DatabaseBackup._validate_restore_point("bad point; rm")

    def test_restore_point_checks_exact_wal_file_after_archiver_advances(self):
        runner = FakeRunner(
            archive_status="on|000000010000000000000002||1h|2|0"
        )
        self.tool(runner).create_restore_point("archiver_advanced")
        expected = "/var/lib/postgresql/backup/wal/000000010000000000000001"
        self.assertTrue(any(expected in command for command in runner.commands))

    def test_short_control_command_timeout_is_actionable(self):
        def timeout_runner(arguments, timeout=None):
            raise subprocess.TimeoutExpired(arguments, timeout)

        with self.assertRaisesRegex(app_backup.CommandError, "Live PostgreSQL role check timed out after 30 seconds"):
            self.tool(timeout_runner).database_state()


class BackupVolume(FakeRunner):
    """A primary whose backup volume holds the given base backups; records what the pruning deletes."""

    def __init__(self, names, latest, labels=None, **kwargs):
        super().__init__(**kwargs)
        self.names, self.latest = list(names), latest
        self.labels = labels if labels is not None else {
            name: f"START WAL LOCATION: 0/{index + 2}000028 (file 0000000100000000000000{index + 2:02X})\n"
                  "CHECKPOINT LOCATION: 0/2000060\n" for index, name in enumerate(self.names)}
        self.deleted = self.cleaned = None

    def __call__(self, arguments, timeout=None):
        command = list(arguments)
        if command[:2] == ["podman", "exec"]:
            if 'cat "$1"; ls "$2"' in command:
                self.commands.append(command)
                return completed("\n".join([self.latest, *self.names, "lost+found"]) + "\n")
            if command[3] == "cat" and command[4].endswith("/backup_label"):
                self.commands.append(command)
                return completed(self.labels.get(command[4].split("/")[-2], ""), returncode=0)
            if any("rm -rf" in part for part in command):
                self.commands.append(command)
                self.deleted = command[command.index(app_backup.backups.BASE) + 1:]
                return completed()
            if command[3] == "pg_archivecleanup":
                self.commands.append(command)
                self.cleaned = command[4:]
                return completed()
        return super().__call__(arguments, timeout)


NOW = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)
WEEK = ["base-20261001T023000Z", "base-20261002T023000Z", "base-20261003T023000Z", "base-20261008T023000Z",
        "base-20261009T030000Z"]


class PruneTests(unittest.TestCase):
    """nightly and prune on a DR primary: backups of the last N days, and only the WAL they need."""

    def prune(self, runner, days=7):
        tool = backup(self, runner, clock=lambda: NOW)
        return tool.prune(days)

    def test_old_backups_go_first_then_the_wal_before_the_oldest_kept_backup(self):
        runner = BackupVolume(WEEK, WEEK[-1])
        self.assertEqual(self.prune(runner), ["base-20261001T023000Z", "base-20261002T023000Z"])
        self.assertEqual(runner.deleted, WEEK[:2])
        # base-20261003 is the oldest kept; its label names WAL file ...04.
        self.assertEqual(runner.cleaned, [app_backup.WAL_ARCHIVE, "000000010000000000000004"])
        steps = [("label" if "backup_label" in " ".join(c) else "delete" if any("rm -rf" in p for p in c)
                  else "wal" if "pg_archivecleanup" in c else None) for c in runner.commands]
        self.assertEqual([step for step in steps if step], ["label", "delete", "wal"])
        self.assertTrue(all(c[2] == "todo-postgres" for c in runner.commands if c[:2] == ["podman", "exec"]))

    def test_nothing_expired_still_cleans_the_wal_older_than_every_backup(self):
        runner = BackupVolume(WEEK[3:], WEEK[-1])
        self.assertEqual(self.prune(runner), [])
        self.assertIsNone(runner.deleted)
        self.assertEqual(runner.cleaned[-1], "000000010000000000000002")

    def test_the_latest_backup_survives_however_old_it_is(self):
        runner = BackupVolume(WEEK[:2], WEEK[1])
        self.assertEqual(self.prune(runner), [WEEK[0]])

    def test_a_missing_latest_or_unreadable_label_deletes_nothing(self):
        for runner, message in ((BackupVolume(WEEK[:-1], WEEK[-1]), "latest verified backup"),
                                (BackupVolume(WEEK, "garbage"), "latest verified backup"),
                                (BackupVolume(WEEK, WEEK[-1], labels={}), "no readable START WAL LOCATION")):
            with self.subTest(message=message), self.assertRaisesRegex(app_backup.BackupError, message):
                self.prune(runner)
            self.assertIsNone(runner.deleted)
            self.assertIsNone(runner.cleaned)

    def test_nightly_backs_up_then_prunes_each_database_and_skips_a_standby(self):
        databases = list(app_backup.apps.REPLICATED_DATABASES)
        runner = BackupVolume(WEEK, WEEK[-1])
        route_commands(self, runner)
        tools = [app_backup.DatabaseBackup(database=database, clock=lambda: NOW) for database in databases]
        lines = app_backup.nightly(tools, 7)
        self.assertEqual(len(lines), len(databases))
        self.assertTrue(lines[0].startswith("todo: verified base backup base-20261009T030000Z; deleted 2 older"))
        order = [("basebackup" if "pg_basebackup" in command else "wal" if "pg_archivecleanup" in command
                  else None) for command in runner.commands]
        self.assertEqual([step for step in order if step], ["basebackup", "wal"] * len(databases))
        standby = BackupVolume(WEEK, WEEK[-1], recovery="t|on")
        route_commands(self, standby)
        tools = [app_backup.DatabaseBackup(database=database, clock=lambda: NOW) for database in databases]
        self.assertEqual(app_backup.nightly(tools, 7), ["standby: nothing to back up; the primary takes the backups"])
        self.assertFalse([command for command in standby.commands if "pg_basebackup" in command])

    def test_nightly_without_archiving_backs_up_nothing(self):
        runner = BackupVolume(WEEK, WEEK[-1], archive_status="off||||0|0")
        route_commands(self, runner)
        tools = [app_backup.DatabaseBackup(database=database) for database in app_backup.apps.REPLICATED_DATABASES]
        with self.assertRaisesRegex(app_backup.BackupError, "todo: archive_mode is not on"):
            app_backup.nightly(tools, 7)
        self.assertFalse([command for command in runner.commands if "pg_basebackup" in command])

    def test_the_command_covers_the_group_and_keeps_at_least_a_day(self):
        for arguments in (["--app", "todo", "nightly", "--keep-days", "7"], ["nightly", "--keep-days", "0"]):
            with self.subTest(arguments=arguments), mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual(app_backup.main(arguments), 1)
            self.assertIn("complete database group", stderr.getvalue())


class CommandLineTests(unittest.TestCase):
    def test_a_failed_command_is_printed_as_an_error_not_a_traceback(self):
        route_commands(self, lambda arguments, timeout: completed(stderr="connection refused", returncode=2))
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(app_backup.main(["--app", "todo", "status"]), 1)
        self.assertIn("ERROR: Live PostgreSQL role check failed (exit 2): connection refused", stderr.getvalue())


class RestoreEdgeTests(unittest.TestCase):
    """Replacing old restore state, failed starts and timeouts never touch live data."""

    BACKUP = "base-20260829T123456Z"

    def tool(self, runner):
        return backup(self, runner, **fake_time())

    def live_names(self, tool):
        app = tool.database
        return {app.container, app.volume("data"), app.volume("backup")}

    def removals(self, runner):
        return [command for command in runner.commands
                if command[:2] == ["podman", "rm"] or command[:3] == ["podman", "volume", "rm"]]

    def test_replace_removes_only_the_old_restore_state_then_restores(self):
        tool = self.tool(None)
        runner = FakeRunner(containers={tool.restore_container}, volumes={tool.restore_volume})
        route_commands(self, runner)
        tool.restore(self.BACKUP, "before_delete", replace=True)
        self.assertEqual(self.removals(runner), [
            ["podman", "rm", "--force", tool.restore_container],
            ["podman", "volume", "rm", tool.restore_volume]])
        create = runner.commands.index(["podman", "volume", "create", tool.restore_volume])
        self.assertGreater(create, runner.commands.index(["podman", "volume", "rm", tool.restore_volume]))
        removed = {name for command in self.removals(runner) for name in command}
        self.assertFalse(removed & self.live_names(tool))

    def test_replace_without_old_state_removes_nothing(self):
        runner = FakeRunner()
        self.tool(runner).restore(self.BACKUP, "before_delete", replace=True)
        self.assertEqual(self.removals(runner), [])

    def test_a_missing_base_backup_refuses_before_old_state_is_removed(self):
        class MissingBackup(FakeRunner):
            def __call__(self, arguments, timeout=None):
                if "PG_VERSION" in " ".join(arguments):
                    self.commands.append(list(arguments))
                    return completed(returncode=1, stderr="no such backup")
                return super().__call__(arguments, timeout)

        tool = self.tool(None)
        runner = MissingBackup(containers={tool.restore_container}, volumes={tool.restore_volume})
        route_commands(self, runner)
        with self.assertRaisesRegex(app_backup.CommandError, "Selected base backup check failed"):
            tool.restore(self.BACKUP, "before_delete", replace=True)
        self.assertEqual(self.removals(runner), [])

    def test_a_failed_start_removes_only_the_new_restore_container(self):
        class FailingStart(FakeRunner):
            def __call__(self, arguments, timeout=None):
                if arguments[:3] == ["podman", "run", "--detach"]:
                    self.commands.append(list(arguments))
                    return completed(returncode=125, stderr="cannot start")
                return super().__call__(arguments, timeout)

        runner = FailingStart()
        tool = self.tool(runner)
        with self.assertRaisesRegex(app_backup.CommandError, r"Disposable PITR container start failed \(exit 125\): cannot start"):
            tool.restore(self.BACKUP, "before_delete", replace=False)
        self.assertEqual(self.removals(runner), [["podman", "rm", "--force", tool.restore_container]])

    def test_a_failed_cleanup_does_not_hide_why_the_start_failed(self):
        class FailingStartAndRemoval(FakeRunner):
            def __init__(self, removal):
                super().__init__()
                self.removal = removal

            def __call__(self, arguments, timeout=None):
                if arguments[:3] == ["podman", "run", "--detach"]:
                    return completed(returncode=125, stderr="cannot start")
                if arguments[:3] == ["podman", "rm", "--force"]:
                    if isinstance(self.removal, Exception):
                        raise self.removal
                    return self.removal
                return super().__call__(arguments, timeout)

        for removal in (completed(returncode=1, stderr="busy"), subprocess.TimeoutExpired("podman", 30)):
            with self.subTest(removal=removal):
                tool = self.tool(FailingStartAndRemoval(removal))
                with self.assertRaisesRegex(
                        app_backup.BackupError,
                        r"Disposable PITR container start failed \(exit 125\): cannot start; removing "
                        + tool.restore_container + " afterwards failed too") as raised:
                    tool.restore(self.BACKUP, "before_delete", replace=False)
                self.assertIsInstance(raised.exception.__cause__, app_backup.CommandError)

    def test_a_restore_that_never_pauses_or_stops_early_is_reported(self):
        class NeverPaused(FakeRunner):
            def __init__(self, stops):
                super().__init__()
                self.stops = stops

            def __call__(self, arguments, timeout=None):
                if "pg_is_wal_replay_paused" in arguments[-1]:
                    self.commands.append(list(arguments))
                    return completed("t|f\n")
                if arguments[:3] == ["podman", "container", "exists"]:
                    self.commands.append(list(arguments))
                    return completed(returncode=1 if self.stops else 0)
                return super().__call__(arguments, timeout)

        for stops, message in ((False, "did not reach its target within 60 seconds"),
                               (True, "stopped during recovery")):
            with self.subTest(stops=stops):
                runner = NeverPaused(stops)
                with self.assertRaisesRegex(app_backup.BackupError, message):
                    self.tool(runner)._wait_for_restore_pause()

    def test_the_restore_wait_retries_only_while_psql_cannot_connect(self):
        # psql exits 2 when the server refuses connections (still starting): wait.
        # Exit 1 is a fatal error in psql itself: report it at once, not after 60 seconds.
        class Starting(FakeRunner):
            def __init__(self, first_code):
                super().__init__(containers={app_backup.apps.APPS[0].names.resource("postgres-restore")})
                self.first_code, self.polls = first_code, 0

            def __call__(self, arguments, timeout=None):
                if "pg_is_wal_replay_paused" in arguments[-1]:
                    self.polls += 1
                    if self.polls == 1:
                        return completed(stderr="psql: error", returncode=self.first_code)
                    return completed("t|t\n")
                return super().__call__(arguments, timeout)

        runner = Starting(2)
        self.tool(runner)._wait_for_restore_pause()
        self.assertEqual(runner.polls, 2)
        runner = Starting(1)
        with self.assertRaisesRegex(app_backup.CommandError, r"PITR status query failed \(exit 1\): psql: error"):
            self.tool(runner)._wait_for_restore_pause()
        self.assertEqual(runner.polls, 1)

    def test_the_restore_wait_is_one_deadline_even_when_each_query_is_slow(self):
        time = FakeTime()
        limits = []

        def slow(arguments, timeout):
            limits.append(timeout)
            time.now += 20
            return completed("t|f\n") if arguments[1] == "exec" else completed()

        tool = backup(self, slow, sleeper=time.sleep, monotonic=time.monotonic)
        with self.assertRaisesRegex(app_backup.BackupError, "within 60 seconds"):
            tool._wait_for_restore_pause()
        self.assertLessEqual(time.now, 60 + 2 * 20 + 1)
        self.assertEqual(limits[0], 60)
        self.assertLess(limits[2], limits[0])

    def test_restore_status_without_a_restore_container(self):
        with self.assertRaisesRegex(app_backup.BackupError, "does not exist"):
            self.tool(FakeRunner()).restore_status()

    def test_wal_that_is_never_archived_or_cannot_be_inspected(self):
        for code, message in ((1, "was not archived within 30 seconds"),
                              (125, r"WAL archive inspection failed \(exit 125\): boom")):
            with self.subTest(code=code):
                def runner(arguments, timeout=None, code=code):
                    return completed(returncode=code, stderr="boom")
                with self.assertRaisesRegex(RuntimeError, message):
                    self.tool(runner)._wait_for_archived_wal("000000010000000000000001")
        with self.assertRaisesRegex(app_backup.BackupError, "Unexpected WAL segment name"):
            self.tool(FakeRunner())._wait_for_archived_wal("../etc/passwd")

    def test_timeouts_during_restore_waits_are_operator_errors(self):
        def timeout_runner(arguments, timeout=None):
            raise subprocess.TimeoutExpired(arguments, timeout)

        tool = self.tool(timeout_runner)
        for call, message in ((tool._wait_for_restore_pause, "PITR status query timed out"),
                              (lambda: tool._wait_for_archived_wal("000000010000000000000001"),
                               "WAL archive inspection timed out"),
                              (lambda: tool._exists("volume", "x"), "inspection timed out")):
            with self.subTest(message=message), self.assertRaisesRegex(app_backup.CommandError, message):
                call()


class TargetTimeTests(unittest.TestCase):
    """Restore to a time: the time is checked, the base backup chosen, and the latest WAL archived first."""

    NOW = datetime(2026, 10, 4, 12, 40, 0, tzinfo=timezone.utc)
    BACKUPS = ("base-20261003T023000Z", "base-20261004T023000Z", "base-20261004T124500Z")

    def tool(self, runner):
        return backup(self, runner, clock=lambda: self.NOW, **fake_time())

    def test_a_time_needs_its_offset_and_must_be_in_the_past(self):
        parse = app_backup.parse_target_time
        self.assertEqual(parse("2026-10-04T14:36:00+02:00", self.NOW), datetime(2026, 10, 4, 12, 36, tzinfo=timezone.utc))
        self.assertEqual(parse("2026-10-04 12:36:00Z", self.NOW), datetime(2026, 10, 4, 12, 36, tzinfo=timezone.utc))
        for text, message in (("2026-10-04T14:36:00", "needs its UTC offset"), ("yesterday", "Not an ISO 8601"),
                              ("2026-10-04T12:41:00Z", "not in the past")):
            with self.subTest(text=text), self.assertRaisesRegex(app_backup.BackupError, message):
                parse(text, self.NOW)

    def test_the_newest_backup_before_the_time_is_restored_after_the_live_wal_is_archived(self):
        runner = FakeRunner(backups=self.BACKUPS)
        chosen = self.tool(runner).restore(None, None, False, target_time="2026-10-04T14:36:00+02:00")
        self.assertEqual(chosen, "base-20261004T023000Z")
        commands = runner.commands
        point = next(i for i, command in enumerate(commands)
                     if "SELECT pg_create_restore_point('archive_before_restore');" in command)
        switch = next(i for i, command in enumerate(commands) if "SELECT pg_switch_wal();" in command)
        self.assertLess(point, switch)
        copy = next(i for i, command in enumerate(commands) if any("cp -a" in part for part in command))
        self.assertLess(switch, copy)
        start = next(command for command in commands if "--detach" in command)
        self.assertIn("recovery_target_time=2026-10-04 12:36:00+00", start)
        self.assertFalse(any(part.startswith("recovery_target_name=") for part in start))
        self.assertIn("base-20261004T023000Z", next(command for command in commands
                                                    if any("cp -a" in part for part in command)))

    def test_refusals_change_nothing(self):
        cases = (
            (dict(backup=None, target=None, target_time=None), "either a named restore point or a target time"),
            (dict(backup=None, target="before_delete", target_time=None), "needs --backup"),
            (dict(backup=None, target=None, target_time="2026-10-03T01:00:00Z"), "No base backup was taken before"),
            (dict(backup="base-20261004T124500Z", target=None, target_time="2026-10-04T12:36:00Z"),
             "was taken after the target time"),
        )
        for arguments, message in cases:
            runner = FakeRunner(backups=self.BACKUPS)
            with self.subTest(message=message), self.assertRaisesRegex(app_backup.BackupError, message):
                self.tool(runner).restore(arguments["backup"], arguments["target"], False,
                                          target_time=arguments["target_time"])
            self.assertFalse(any("pg_switch_wal" in " ".join(command) or "create" in command
                                 for command in runner.commands))

    def test_the_command_line_takes_a_time_without_a_backup(self):
        runner = FakeRunner(backups=self.BACKUPS)
        route_commands(self, runner)
        with mock.patch.object(app_backup, "datetime", wraps=datetime) as clock, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            clock.now.return_value = self.NOW
            self.assertEqual(app_backup.main(["--app", "notes", "restore", "--target-time", "2026-10-04T12:36:00Z"]), 0)
        self.assertIn("notes: PITR from base-20261004T023000Z paused at 2026-10-04T12:36:00Z", stdout.getvalue())
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            app_backup.main(["--app", "notes", "restore", "--target", "x", "--target-time", "2026-10-04T12:36:00Z"])


class ApplicationBackupTests(unittest.TestCase):
    def test_each_backup_and_restore_stays_within_its_app(self):
        for app in app_backup.apps.REPLICATED_DATABASES:
            runner = FakeRunner()
            tool = backup(self, runner, database=app)
            tool.create_backup()
            tool.restore('base-20260829T123456Z', 'before_delete', False)
            commands = runner.commands
            # The backup runs inside the database's own container, over its local socket.
            basebackup = next(command for command in commands if 'pg_basebackup' in command)
            self.assertEqual(basebackup[:3], ['podman', 'exec', app.container])
            self.assertIn('--username=' + app.name, basebackup)
            for other in app_backup.apps.REPLICATED_DATABASES:
                if other == app:
                    continue
                self.assertFalse(any(other.container in argument
                                     for command in commands for argument in command))
            self.assertFalse(any(app.volume('data') in argument
                                 for command in commands for argument in command))
            self.assertTrue(any('--network' in command and 'none' in command for command in commands))
            self.assertTrue(any('recovery_target_action=pause' in command for command in commands))

    def test_cleanup_cannot_target_the_other_apps_restore(self):
        for app in app_backup.apps.REPLICATED_DATABASES:
            runner = FakeRunner(containers={app.names.resource('postgres-restore')},
                                volumes={app.volume('restore-data')})
            tool = backup(self, runner, database=app)
            with self.assertRaises(app_backup.BackupError):
                tool.cleanup_restore('yes')
            self.assertEqual(runner.commands, [])
            tool.cleanup_restore(app.names.resource('postgres-restore'))
            removals = [command for command in runner.commands if 'rm' in command]
            self.assertEqual(removals, [
                ['podman', 'rm', '--force', app.names.resource('postgres-restore')],
                ['podman', 'volume', 'rm', app.volume('restore-data')]])

    def test_group_backup_checks_last_app_before_first_base_backup(self):
        instances = [mock.Mock(), mock.Mock(), mock.Mock()]
        instances[0].archive_status.return_value = 'on|'
        instances[1].archive_status.return_value = 'on|'
        instances[2].require_writable_primary.side_effect = app_backup.BackupError('keycloak is read-only')
        with mock.patch.object(app_backup, 'DatabaseBackup', side_effect=instances):
            self.assertEqual(app_backup.main(['create']), 1)
        for tool in instances:
            tool.create_backup.assert_not_called()


class FakeHost:
    """One promoted host running every registered database, for the configure command."""

    def __init__(self, configured=(), directories_ready=(), mounts=None, source=None, active=True, previous=None):
        self.databases = {d.container: d for d in app_backup.apps.REPLICATED_DATABASES}
        self.configured = set(configured)
        # Containers that already archive, with an older archive command.
        self.previous = previous or {}
        self.directories_ready = set(directories_ready)
        self.mounts = mounts or {}
        self.source = source or {}
        self.active = active
        self.commands = []

    def container_for(self, command):
        return next((c for c in command if c in self.databases), None)

    def __call__(self, arguments, timeout=None):
        command = list(arguments)
        self.commands.append(command)
        container = self.container_for(command)
        if command[:3] == ["systemctl", "--user", "is-active"]:
            return completed("active\n" if self.active else "inactive\n", returncode=0 if self.active else 3)
        if command[:3] == ["systemctl", "--user", "show"]:
            database = next(d for d in self.databases.values() if d.service == command[3])
            default = "/home/u/.config/containers/systemd/todo-kube-runtime/" + database.unit
            return completed(self.source.get(database.name, default) + "\n")
        if command[:2] == ["systemctl", "--user"]:
            return completed()
        if command[:3] in (["podman", "secret", "exists"], ["podman", "volume", "exists"]):
            return completed()
        if command[:2] == ["podman", "inspect"]:
            database = self.databases[container]
            good = [{"Type": "volume", "Name": database.volume('backup'),
                     "Destination": "/var/lib/postgresql/backup", "RW": True}]
            return completed(json.dumps(self.mounts.get(database.name, good)))
        if command[:2] == ["podman", "run"] and app_backup.BACKUP_DIRECTORIES_SCRIPT in command:
            volume = command[command.index("--volume") + 1].split(":")[0]
            ready = volume in self.directories_ready
            self.directories_ready.add(volume)
            return completed("unchanged\n" if ready else "changed\n")
        if "psql" in command:
            sql = " ".join(command[command.index("psql"):])
            if "ALTER SYSTEM" in sql:
                self.configured.add(container)
                return completed("ALTER SYSTEM\n")
            if "current_setting('archive_command')" in sql:
                if container in self.configured:
                    return completed(f"on|{app_backup.ARCHIVE_COMMAND}|{app_backup.ARCHIVE_TIMEOUT}\n")
                if container in self.previous:
                    return completed(f"on|{self.previous[container]}|{app_backup.ARCHIVE_TIMEOUT}\n")
                return completed("off|(disabled)|0\n")
            if "pg_is_in_recovery" in sql:
                return completed("f|off\n")
            if "pg_create_restore_point" in sql:
                return completed("0/5000100\n")
            if "pg_walfile_name" in sql:
                return completed("000000010000000000000001\n")
        return completed()

    def index(self, predicate):
        return next(i for i, command in enumerate(self.commands) if predicate(command))

    def matching(self, predicate):
        return [command for command in self.commands if predicate(command)]


class ConfigureArchiveTests(unittest.TestCase):
    DATABASES = app_backup.apps.REPLICATED_DATABASES

    def configure(self, host, promoted=True, access_changed=False, recorded=None):
        tools = [backup(self, host, database=app, **fake_time(),
                                        clock=lambda: datetime(2026, 9, 25, 12, 0, 0, 123456, tzinfo=timezone.utc))
                 for app in self.DATABASES]
        self.hba = []
        self.waits = []
        journal = mock.Mock(side_effect=None if promoted else RuntimeError('A readable completed group '
                                                                                'promotion record is required'))
        with mock.patch.object(app_backup.replication, 'require_promoted_group', journal), \
                mock.patch.object(app_backup.replication, 'refresh_hba',
                                  side_effect=lambda app: (self.hba.append((len(host.commands), app.name)), access_changed)[1]), \
                mock.patch.object(app_backup.target_render, 'read_record', return_value=recorded or {}), \
                mock.patch.object(app_backup.keycloak, 'wait',
                                  side_effect=lambda path, *a, **k: self.waits.append((path, k.get('hostname')))):
            return app_backup.configure(tools, Path('/journal.json'))

    def archive(self, archive, wal_name, content, env=None):
        """Run ARCHIVE_COMMAND with /bin/sh as PostgreSQL does, with the archive at archive; return its exit code."""
        source = archive.parent / "pg_wal" / wal_name
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(content)
        command = (app_backup.ARCHIVE_COMMAND.replace(app_backup.WAL_ARCHIVE, str(archive))
                   .replace("%p", str(source)).replace("%f", wal_name))
        return subprocess.run(["/bin/sh", "-c", command], capture_output=True, env=env).returncode

    def test_the_archive_command_copies_durably_and_never_overwrites(self):
        import tempfile
        self.assertNotIn("|", app_backup.ARCHIVE_COMMAND)  # psql separates the settings with "|"
        self.assertEqual(app_backup.ARCHIVE_TIMEOUT, '1h')
        wal = "000000010000000000000003"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "wal"
            archive.mkdir()
            self.assertEqual(self.archive(archive, wal, b"segment"), 0)
            self.assertEqual((archive / wal).read_bytes(), b"segment")
            self.assertEqual([path.name for path in archive.iterdir()], [wal])  # no temporary file left
            # PostgreSQL may archive the same file again after a crash: identical is fine,
            # different must fail and leave the archived copy alone.
            self.assertEqual(self.archive(archive, wal, b"segment"), 0)
            self.assertNotEqual(self.archive(archive, wal, b"other"), 0)
            self.assertEqual((archive / wal).read_bytes(), b"segment")
            # A copy that cannot be synced never shows up under the real name, so
            # PostgreSQL keeps its original and archives it again later.
            failing = Path(directory) / "bin"
            failing.mkdir()
            (failing / "sync").write_text("#!/bin/sh\nexit 1\n")
            (failing / "sync").chmod(0o755)
            import os
            env = {**os.environ, "PATH": f"{failing}:{os.environ['PATH']}"}
            self.assertNotEqual(self.archive(archive, "000000010000000000000004", b"next", env), 0)
            self.assertFalse((archive / "000000010000000000000004").exists())

    def test_the_check_after_a_reload_waits_for_the_new_settings(self):
        class SlowReload(FakeHost):
            """Shows the old command for two more reads after the reload, as a busy server can."""
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.stale = 2

            def __call__(self, arguments, timeout=None):
                sql = arguments[-1] if arguments else ""
                if "current_setting('archive_command')" in sql and self.configured and self.stale:
                    self.stale -= 1
                    return completed("on|old|1h\n")
                return super().__call__(arguments, timeout)

        host = SlowReload(previous={self.DATABASES[0].container: "old"},
                          configured={d.container for d in self.DATABASES[1:]},
                          directories_ready={d.volume('backup') for d in self.DATABASES})
        self.assertEqual(self.configure(host)['restarted'], [])

    def test_a_host_with_the_old_archive_command_is_reloaded_not_restarted(self):
        old = ('test ! -f /var/lib/postgresql/backup/wal/%f && cp %p /var/lib/postgresql/backup/wal/%f || '
               'test "$(sha256sum < %p)" = "$(sha256sum < /var/lib/postgresql/backup/wal/%f)"')
        host = FakeHost(previous={d.container: old for d in self.DATABASES},
                        directories_ready={d.volume('backup') for d in self.DATABASES})
        result = self.configure(host)
        self.assertEqual(result['changed'], True)
        self.assertEqual(result['restarted'], [])
        self.assertEqual(sorted(result['verified']), sorted(d.name for d in self.DATABASES))
        for verb in ("stop", "restart"):
            self.assertEqual(host.matching(lambda c, verb=verb: c[:3] == ["systemctl", "--user", verb]), [])
        reloads = host.matching(lambda c: any('pg_reload_conf' in part for part in c))
        self.assertEqual(len(reloads), len(self.DATABASES))
        self.assertLess(host.commands.index(reloads[-1]),
                        host.index(lambda c: any('pg_create_restore_point' in part for part in c)))

    def test_fresh_group_is_gated_first_then_restarted_behind_one_application_tier_stop(self):
        host = FakeHost()
        result = self.configure(host)
        names = [d.name for d in self.DATABASES]
        self.assertEqual(result['changed'], True)
        self.assertEqual(result['restarted'], names)
        self.assertEqual(sorted(result['verified']), sorted(names))
        self.assertEqual(result['verified']['todo'], 'todo_archive_check_20260925120000123456')
        last_gate = max(i for i, c in enumerate(host.commands) if c[:3] == ["systemctl", "--user", "show"])
        first_write = min([self.hba[0][0], host.index(lambda c: app_backup.BACKUP_DIRECTORIES_SCRIPT in c)])
        self.assertLess(last_gate, first_write)
        stops = host.matching(lambda c: c[:3] == ["systemctl", "--user", "stop"])
        self.assertEqual([c[3] for c in stops], app_backup.apps.services(databases=False))
        restarts = host.matching(lambda c: c[:3] == ["systemctl", "--user", "restart"])
        self.assertEqual([c[3] for c in restarts], [d.service for d in self.DATABASES])
        start = host.index(lambda c: c == ["systemctl", "--user", "start", "keycloak.service", "todo-app.service", "notes-app.service", "shared-proxy.service"])
        self.assertLess(host.index(lambda c: c[:3] == ["systemctl", "--user", "stop"]),
                        host.index(lambda c: c[:3] == ["systemctl", "--user", "restart"]))
        self.assertLess(max(host.commands.index(c) for c in restarts), start)
        self.assertLess(start, host.index(lambda c: any('pg_create_restore_point' in part for part in c)))
        self.assertEqual(self.waits[:len(app_backup.apps.APPS)],
                         [('/ready', app.hostname) for app in app_backup.apps.APPS])

    def test_readiness_uses_the_hostnames_this_host_serves(self):
        host = FakeHost()
        self.configure(host, recorded={'TARGET_EXTERNAL_HOSTNAME': 'todo.example.org',
                                       'TARGET_NOTES_HOSTNAME': 'notes.example.org'})
        self.assertEqual(self.waits[:2], [('/ready', 'todo.example.org'), ('/ready', 'notes.example.org')])

    def test_configured_group_is_left_running_and_unverified(self):
        host = FakeHost(configured={d.container for d in self.DATABASES},
                        directories_ready={d.volume('backup') for d in self.DATABASES})
        result = self.configure(host)
        self.assertEqual(result, {'changed': False, 'restarted': [], 'verified': {}})
        for verb in ("stop", "restart"):
            self.assertEqual(host.matching(lambda c, verb=verb: c[:3] == ["systemctl", "--user", verb]), [])
        self.assertFalse(host.matching(lambda c: any('ALTER SYSTEM' in part or 'pg_switch_wal' in part
                                                     for part in c)))
        self.assertTrue(host.matching(lambda c: c == ["systemctl", "--user", "start", "keycloak.service", "todo-app.service", "notes-app.service", "shared-proxy.service"]))

    def test_changed_replication_access_alone_reports_change_without_restart(self):
        host = FakeHost(configured={d.container for d in self.DATABASES},
                        directories_ready={d.volume('backup') for d in self.DATABASES})
        self.assertEqual(self.configure(host, access_changed=True),
                         {'changed': True, 'restarted': [], 'verified': {}})

    def test_invalid_mount_json_or_a_hung_stop_is_an_operator_error(self):
        class BrokenInspect(FakeHost):
            def __call__(self, arguments, timeout=None):
                if list(arguments[:2]) == ["podman", "inspect"]:
                    return completed("not json")
                return super().__call__(arguments, timeout)

        class HungStop(FakeHost):
            def __call__(self, arguments, timeout=None):
                if list(arguments[:3]) == ["systemctl", "--user", "stop"]:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                return super().__call__(arguments, timeout)

        with self.assertRaisesRegex(app_backup.BackupError, 'invalid JSON'):
            self.configure(BrokenInspect())
        with self.assertRaises(app_backup.BackupError):
            self.configure(HungStop())

    def test_only_the_changed_database_restarts_and_is_verified(self):
        last = self.DATABASES[-1]
        host = FakeHost(configured={d.container for d in self.DATABASES[:-1]},
                        directories_ready={d.volume('backup') for d in self.DATABASES})
        result = self.configure(host)
        self.assertEqual(result['restarted'], [last.name])
        self.assertEqual(list(result['verified']), [last.name])
        self.assertEqual(len(host.matching(lambda c: c[:3] == ["systemctl", "--user", "stop"])),
                         len(app_backup.apps.services(databases=False)))

    def test_a_failed_gate_on_the_last_database_stops_before_any_write(self):
        host = FakeHost(mounts={self.DATABASES[-1].name: []})
        with self.assertRaisesRegex(app_backup.BackupError, 'writable backup PVC'):
            self.configure(host)
        self.assertEqual(self.hba, [])
        self.assertFalse(host.matching(lambda c: app_backup.BACKUP_DIRECTORIES_SCRIPT in c
                                       or any('ALTER SYSTEM' in part for part in c)
                                       or c[:3] == ["systemctl", "--user", "stop"]))

    def test_backup_mount_must_be_exactly_the_writable_pvc_at_the_archive_path(self):
        for database in self.DATABASES:
            other = next(d for d in self.DATABASES if d != database)
            good = {"Type": "volume", "Name": database.volume('backup'),
                    "Destination": "/var/lib/postgresql/backup", "RW": True}
            for mounts, accepted in [([good], True), ([], False), ([good, good], False),
                                     ([{**good, "Name": other.volume('backup')}], False),
                                     ([{**good, "Destination": "/wrong"}], False),
                                     ([{**good, "RW": False}], False), ([{**good, "Type": "bind"}], False)]:
                with self.subTest(database=database.name, mounts=mounts):
                    tool = backup(self, FakeHost(mounts={database.name: mounts}), database=database)
                    if accepted:
                        tool.require_archive_prerequisites()
                    else:
                        with self.assertRaises(app_backup.BackupError):
                            tool.require_archive_prerequisites()

    def test_non_kube_or_inactive_postgres_is_refused(self):
        name = self.DATABASES[0].name
        with self.assertRaisesRegex(app_backup.BackupError, 'non-Kube'):
            self.configure(FakeHost(source={name: '/etc/containers/systemd/todo-postgres.container'}))
        with self.assertRaisesRegex(app_backup.BackupError, 'is not active'):
            self.configure(FakeHost(active=False))

    def test_incomplete_promotion_refuses_before_any_host_command(self):
        host = FakeHost()
        with self.assertRaisesRegex(app_backup.BackupError, 'completed group promotion'):
            self.configure(host, promoted=False)
        self.assertEqual(host.commands, [])

    def test_configure_always_covers_the_complete_group(self):
        self.assertEqual(app_backup.main(['--app', 'todo', 'configure']), 1)


if __name__ == "__main__":
    unittest.main()
