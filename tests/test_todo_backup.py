import importlib.util
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "todo_backup.py"
SPEC = importlib.util.spec_from_file_location("todo_backup", SCRIPT)
assert SPEC and SPEC.loader
todo_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(todo_backup)


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    def __init__(
        self, recovery="f|off", containers=None, volumes=None,
        archive_status="on|000000010000000000000001||1|0",
    ):
        self.recovery = recovery
        self.containers = set(containers or ())
        self.volumes = set(volumes or ())
        self.archive_status = archive_status
        self.commands = []

    def __call__(self, arguments):
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
                "on|000000010000000000000001|000000010000000000000000|2|1"
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


if __name__ == "__main__":
    unittest.main()
