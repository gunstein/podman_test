import importlib.util
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "deploy/scripts" / "todo_backup.py"
SPEC = importlib.util.spec_from_file_location("todo_backup", SCRIPT)
assert SPEC and SPEC.loader
todo_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(todo_backup)


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    def __init__(
        self, recovery="f|off", containers=None, volumes=None,
        archive_status="on|000000010000000000000001||1h|1|0",
    ):
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


class TodoBackupTests(unittest.TestCase):
    def tool(self, runner):
        return todo_backup.TodoBackup(
            runner=runner,
            clock=lambda: datetime(2026, 8, 29, 12, 34, 56, tzinfo=timezone.utc),
            sleeper=lambda _seconds: None,
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
        with self.assertRaisesRegex(todo_backup.BackupError, "not a writable"):
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
        runner = FakeRunner(containers={todo_backup.RESTORE_CONTAINER})
        with self.assertRaisesRegex(todo_backup.BackupError, "--replace"):
            self.tool(runner).restore(
                "base-20260829T123456Z", "before_delete", replace=False
            )

    def test_restore_uses_only_fixed_disposable_targets(self):
        runner = FakeRunner()
        self.tool(runner).restore(
            "base-20260829T123456Z", "before_delete", replace=False
        )
        flattened = [item for command in runner.commands for item in command]
        self.assertIn(todo_backup.RESTORE_CONTAINER, flattened)
        self.assertIn(todo_backup.RESTORE_VOLUME, flattened)
        self.assertNotIn("todo-postgres-data", flattened)

    def test_cleanup_requires_exact_confirmation(self):
        with self.assertRaisesRegex(todo_backup.BackupError, "exactly"):
            self.tool(FakeRunner()).cleanup_restore("yes")

    def test_rejects_unsafe_backup_and_restore_point_names(self):
        with self.assertRaises(todo_backup.BackupError):
            todo_backup.TodoBackup._validate_backup_name("../../data")
        with self.assertRaises(todo_backup.BackupError):
            todo_backup.TodoBackup._validate_restore_point("bad point; rm")

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

        with self.assertRaisesRegex(todo_backup.BackupError, "timed out"):
            self.tool(timeout_runner).database_state()

class ApplicationBackupTests(unittest.TestCase):
    def test_each_backup_and_restore_stays_within_its_app(self):
        for app in todo_backup.apps.REPLICATED_APPS:
            runner = FakeRunner()
            tool = todo_backup.TodoBackup(runner=runner, app=app)
            tool.create_backup()
            tool.restore('base-20260829T123456Z', 'before_delete', False)
            commands = runner.commands
            backup = next(command for command in commands if 'pg_basebackup' in command)
            self.assertIn('--host=' + app.resource('postgres'), backup)
            self.assertIn('--username=' + app.database_role('replicator'), backup)
            self.assertIn(app.secret('replicator') + ',type=env,target=PGPASSWORD', backup)
            self.assertIn(app.volume('backup') + ':/backup:z', backup)
            for other in todo_backup.apps.REPLICATED_APPS:
                if other == app:
                    continue
                self.assertFalse(any(other.resource('postgres') in argument
                                     for command in commands for argument in command))
            self.assertFalse(any(app.volume('data') in argument
                                 for command in commands for argument in command))
            self.assertTrue(any('--network' in command and 'none' in command for command in commands))
            self.assertTrue(any('recovery_target_action=pause' in command for command in commands))

    def test_cleanup_cannot_target_the_other_apps_restore(self):
        for app in todo_backup.apps.REPLICATED_APPS:
            runner = FakeRunner(containers={app.resource('postgres-restore')},
                                volumes={app.volume('restore-data')})
            tool = todo_backup.TodoBackup(runner=runner, app=app)
            with self.assertRaises(todo_backup.BackupError):
                tool.cleanup_restore('yes')
            self.assertEqual(runner.commands, [])
            tool.cleanup_restore(app.resource('postgres-restore'))
            removals = [command for command in runner.commands if 'rm' in command]
            self.assertEqual(removals, [
                ['podman', 'rm', '--force', app.resource('postgres-restore')],
                ['podman', 'volume', 'rm', app.volume('restore-data')]])

    def test_group_backup_checks_last_app_before_first_base_backup(self):
        instances = [mock.Mock(), mock.Mock()]
        instances[0].archive_status.return_value = 'on|'
        instances[1].require_writable_primary.side_effect = todo_backup.BackupError('notes is read-only')
        with mock.patch.object(todo_backup, 'TodoBackup', side_effect=instances):
            self.assertEqual(todo_backup.main(['create']), 1)
        for tool in instances:
            tool.create_backup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
