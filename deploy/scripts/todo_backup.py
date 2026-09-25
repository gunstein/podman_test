"""Independent physical PostgreSQL backups and scoped disposable PITR per app."""

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

# Checkout/operations package and exact-file trusted target installation.
for location in ('installer', 'lib'):
    directory = Path(__file__).resolve().parents[1] / location
    if (directory / 'todo_installer').is_dir():
        sys.path.insert(0, str(directory))
        break
from todo_installer import apps  # noqa: E402

DATA_DIRECTORY = "/var/lib/postgresql/data"
BACKUP_NAME = re.compile(r"base-[0-9]{8}T[0-9]{6}Z")
RESTORE_POINT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}")
WAL_SEGMENT = re.compile(r"[0-9A-F]{24}")


class BackupError(RuntimeError):
    """An expected, operator-actionable backup error."""


Runner = Callable[[Sequence[str], Optional[float]], subprocess.CompletedProcess]


def run_command(
    arguments: Sequence[str], timeout: Optional[float] = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TodoBackup:
    def __init__(
        self,
        runner: Runner = run_command,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleeper: Callable[[float], None] = time.sleep,
        *, app: apps.App = apps.SHARED_RESOURCE_OWNER,
    ) -> None:
        self.app = app
        self.image = app.image('postgres')
        self.backup_volume = app.volume('backup')
        self.restore_volume = app.volume('restore-data')
        self.restore_container = app.resource('postgres-restore')
        self.runner = runner
        self.clock = clock
        self.sleeper = sleeper

    def _run(
        self,
        arguments: Sequence[str],
        description: str,
        timeout: Optional[float] = 30,
    ) -> str:
        try:
            result = self.runner(arguments, timeout)
        except subprocess.TimeoutExpired as error:
            raise BackupError(
                f"{description} timed out after {timeout:g} seconds"
            ) from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            suffix = f": {detail}" if detail else ""
            raise BackupError(f"{description} failed{suffix}")
        return result.stdout.strip()

    def _exists(self, kind: str, name: str) -> bool:
        try:
            result = self.runner(["podman", kind, "exists", name], 30)
        except subprocess.TimeoutExpired as error:
            raise BackupError(
                f"Podman {kind} inspection timed out after 30 seconds"
            ) from error
        if result.returncode not in (0, 1):
            detail = (result.stderr or result.stdout).strip()
            raise BackupError(f"Could not inspect Podman {kind} {name}: {detail}")
        return result.returncode == 0

    def database_state(self) -> tuple[bool, bool]:
        output = self._run(
            [
                "podman", "exec", self.app.resource("postgres"), "psql",
                "--username", self.app.name, "--dbname", "postgres",
                "--tuples-only", "--no-align", "--field-separator=|",
                "--command",
                "SELECT pg_is_in_recovery(), "
                "current_setting('transaction_read_only');",
            ],
            "Live PostgreSQL role check",
        )
        if output not in ("t|on", "f|off"):
            raise BackupError(f"Unexpected live PostgreSQL role: {output!r}")
        recovery, read_only = output.split("|")
        return recovery == "t", read_only == "on"

    def require_writable_primary(self) -> None:
        recovery, read_only = self.database_state()
        if recovery or read_only:
            raise BackupError("Live PostgreSQL is not a writable promoted primary")

    def archive_status(self) -> str:
        return self._run(
            [
                "podman", "exec", self.app.resource("postgres"), "psql",
                "--username", self.app.name, "--dbname", "postgres",
                "--tuples-only", "--no-align", "--field-separator=|",
                "--command",
                "SELECT current_setting('archive_mode'), "
                "COALESCE(last_archived_wal, ''), "
                "COALESCE(last_failed_wal, ''), "
                "current_setting('archive_timeout'), archived_count, failed_count "
                "FROM pg_stat_archiver;",
            ],
            "WAL archive status query",
        )

    def status_lines(self) -> list[str]:
        recovery, read_only = self.database_state()
        archive = self.archive_status().split("|")
        if len(archive) != 6:
            raise BackupError(f"Unexpected WAL archive status: {'|'.join(archive)!r}")
        return [
            f"Database recovery mode: {'yes' if recovery else 'no'}",
            f"Database writable: {'no' if read_only else 'yes'}",
            f"Archive mode: {archive[0]}",
            f"Last archived WAL: {archive[1] or 'none'}",
            f"Last failed WAL: {archive[2] or 'none'}",
            f"Archive timeout: {archive[3]}",
            f"Archived segments: {archive[4]}",
            f"Failed archive attempts: {archive[5]}",
            f"Backup volume: {self.backup_volume}",
        ]

    def create_backup(self) -> str:
        self.require_writable_primary()
        if self.archive_status().split("|", 1)[0] != "on":
            raise BackupError("archive_mode is not on")

        name = self.clock().strftime("base-%Y%m%dT%H%M%SZ")
        self._run(
            [
                "podman", "run", "--rm",
                "--network", apps.NETWORK,
                "--user", "postgres",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "all",
                "--pids-limit", "128",
                "--volume", f"{self.backup_volume}:/backup:z",
                "--secret",
                self.app.secret("replicator") + ",type=env,target=PGPASSWORD",
                self.image,
                "pg_basebackup",
                "--host=" + self.app.resource("postgres"),
                "--port=5432",
                "--username=" + self.app.database_role("replicator"),
                f"--pgdata=/backup/base/{name}",
                "--format=plain",
                "--wal-method=stream",
                "--checkpoint=fast",
                "--manifest-checksums=SHA256",
                "--progress",
            ],
            "Physical base backup",
            timeout=None,
        )
        self._run(
            [
                "podman", "run", "--rm",
                "--user", "postgres",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "all",
                "--volume", f"{self.backup_volume}:/backup:z",
                "--entrypoint", "pg_verifybackup",
                self.image,
                f"/backup/base/{name}",
            ],
            "Base backup verification",
            timeout=None,
        )
        self._run(
            [
                "podman", "run", "--rm",
                "--user", "postgres",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "all",
                "--volume", f"{self.backup_volume}:/backup:z",
                "--entrypoint", "/bin/sh",
                self.image,
                "-ec", 'printf "%s\\n" "$1" > /backup/LATEST',
                self.app.resource("backup"), name,
            ],
            "Latest backup marker update",
        )
        return name

    def create_restore_point(self, name: str) -> str:
        self.require_writable_primary()
        self._validate_restore_point(name)
        output = self._run(
            [
                "podman", "exec", self.app.resource("postgres"), "psql",
                "--username", self.app.name, "--dbname", "postgres",
                "--tuples-only", "--no-align",
                "--command",
                f"SELECT pg_create_restore_point('{name}');",
            ],
            "Named restore point creation",
        )
        wal = self._run(
            [
                "podman", "exec", self.app.resource("postgres"), "psql",
                "--username", self.app.name, "--dbname", "postgres",
                "--tuples-only", "--no-align",
                "--command", "SELECT pg_walfile_name(pg_current_wal_lsn());",
            ],
            "Current WAL segment query",
        )
        self._run(
            [
                "podman", "exec", self.app.resource("postgres"), "psql",
                "--username", self.app.name, "--dbname", "postgres",
                "--tuples-only", "--no-align",
                "--command", "SELECT pg_switch_wal();",
            ],
            "WAL switch after restore point",
        )
        self._wait_for_archived_wal(wal)
        return output

    def _wait_for_archived_wal(self, wal: str) -> None:
        if not WAL_SEGMENT.fullmatch(wal):
            raise BackupError(f"Unexpected WAL segment name: {wal!r}")
        archive_path = f"/var/lib/postgresql/backup/wal/{wal}"
        for _attempt in range(30):
            try:
                result = self.runner(
                    ["podman", "exec", self.app.resource("postgres"), "test", "-f", archive_path],
                    30,
                )
            except subprocess.TimeoutExpired as error:
                raise BackupError(
                    "WAL archive inspection timed out after 30 seconds"
                ) from error
            if result.returncode == 0:
                return
            if result.returncode != 1:
                detail = (result.stderr or result.stdout).strip()
                raise BackupError(f"WAL archive inspection failed: {detail}")
            self.sleeper(1)
        raise BackupError(f"WAL segment was not archived within 30 seconds: {wal}")

    def restore(self, backup: str, target: str, replace: bool) -> None:
        self._validate_backup_name(backup)
        self._validate_restore_point(target)
        self._run(
            [
                "podman", "run", "--rm",
                "--user", "postgres",
                "--volume", f"{self.backup_volume}:/backup:ro,z",
                "--entrypoint", "/bin/sh",
                self.image,
                "-ec", 'test -s "/backup/base/$1/PG_VERSION"',
                self.app.resource("backup"), backup,
            ],
            "Selected base backup check",
        )

        container_exists = self._exists("container", self.restore_container)
        volume_exists = self._exists("volume", self.restore_volume)
        if (container_exists or volume_exists) and not replace:
            raise BackupError(
                "Disposable restore state already exists; rerun with --replace "
                "only after confirming it can be deleted"
            )
        if replace:
            if container_exists:
                self._run(
                    ["podman", "rm", "--force", self.restore_container],
                    "Old restore container removal",
                )
            if volume_exists:
                self._run(
                    ["podman", "volume", "rm", self.restore_volume],
                    "Old restore volume removal",
                )

        self._run(
            ["podman", "volume", "create", self.restore_volume],
            "Disposable restore volume creation",
        )
        try:
            self._run(
                [
                    "podman", "run", "--rm",
                    "--user", "postgres",
                    "--security-opt", "no-new-privileges",
                    "--cap-drop", "all",
                    "--volume", f"{self.backup_volume}:/backup:ro,z",
                    "--volume", f"{self.restore_volume}:/restore:U,Z",
                    "--entrypoint", "/bin/sh",
                    self.image,
                    "-ec",
                    'cp -a "/backup/base/$1/." /restore/; '
                    "rm -f /restore/standby.signal /restore/recovery.signal; "
                    "touch /restore/recovery.signal; chmod 0700 /restore",
                    self.app.resource("backup"), backup,
                ],
                "Base backup copy into disposable restore volume",
                timeout=None,
            )
            self._run(
                [
                    "podman", "run", "--detach",
                    "--name", self.restore_container,
                    "--network", "none",
                    "--user", "postgres",
                    "--security-opt", "no-new-privileges",
                    "--cap-drop", "all",
                    "--pids-limit", "128",
                    "--volume", f"{self.restore_volume}:{DATA_DIRECTORY}:Z",
                    "--volume",
                    f"{self.backup_volume}:/var/lib/postgresql/backup:ro,z",
                    "--entrypoint", "postgres",
                    self.image,
                    "-D", DATA_DIRECTORY,
                    "-c",
                    "restore_command=cp /var/lib/postgresql/backup/wal/%f %p",
                    "-c", f"recovery_target_name={target}",
                    "-c", "recovery_target_action=pause",
                    "-c", "recovery_target_timeline=latest",
                    "-c", "primary_conninfo=",
                    "-c", "primary_slot_name=",
                    "-c", "archive_mode=off",
                    "-c", "listen_addresses=",
                ],
                "Disposable PITR container start",
            )
            self._wait_for_restore_pause()
        except Exception:
            self.runner(["podman", "rm", "--force", self.restore_container], 30)
            raise

    def _wait_for_restore_pause(self) -> None:
        for _attempt in range(60):
            try:
                result = self.runner(
                    [
                        "podman", "exec", self.restore_container,
                        "psql", "--username", self.app.name, "--dbname", "postgres",
                        "--tuples-only", "--no-align", "--field-separator=|",
                        "--command",
                        "SELECT pg_is_in_recovery(), pg_is_wal_replay_paused();",
                    ],
                    30,
                )
            except subprocess.TimeoutExpired as error:
                raise BackupError(
                    "Disposable PITR status query timed out after 30 seconds"
                ) from error
            if result.returncode == 0 and result.stdout.strip() == "t|t":
                return
            if not self._exists("container", self.restore_container):
                raise BackupError("Disposable PITR container stopped during recovery")
            self.sleeper(1)
        raise BackupError("PITR did not reach the named restore point within 60 seconds")

    def restore_status(self) -> str:
        if not self._exists("container", self.restore_container):
            raise BackupError("Disposable PITR container does not exist")
        return self._run(
            [
                "podman", "exec", self.restore_container,
                "psql", "--username", self.app.name, "--dbname", "postgres",
                "--tuples-only", "--no-align", "--field-separator=|",
                "--command",
                "SELECT pg_is_in_recovery(), pg_is_wal_replay_paused(), "
                "current_setting('transaction_read_only');",
            ],
            "Disposable PITR status query",
        )

    def cleanup_restore(self, confirmation: str) -> None:
        if confirmation != self.restore_container:
            raise BackupError(
                f"Cleanup confirmation must be exactly {self.restore_container!r}"
            )
        if self._exists("container", self.restore_container):
            self._run(
                ["podman", "rm", "--force", self.restore_container],
                "Disposable restore container removal",
            )
        if self._exists("volume", self.restore_volume):
            self._run(
                ["podman", "volume", "rm", self.restore_volume],
                "Disposable restore volume removal",
            )

    @staticmethod
    def _validate_backup_name(name: str) -> None:
        if not BACKUP_NAME.fullmatch(name):
            raise BackupError("Invalid base backup name")

    @staticmethod
    def _validate_restore_point(name: str) -> None:
        if not RESTORE_POINT.fullmatch(name):
            raise BackupError(
                "Restore point must start with a letter and contain only "
                "letters, digits, underscore or hyphen"
            )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Manage independent application backups and disposable PITR."
    )
    result.add_argument('--app', choices=[d.name for d in apps.REPLICATED_DATABASES],
                        help='Select one app; status/create/mark default to the whole group')
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show live database and WAL archive status")
    commands.add_parser("create", help="Create and verify a physical base backup")
    mark = commands.add_parser("mark", help="Create and archive a named restore point")
    mark.add_argument("--name", required=True)
    restore = commands.add_parser(
        "restore", help="Restore into an isolated disposable PostgreSQL container"
    )
    restore.add_argument("--backup", required=True)
    restore.add_argument("--target", required=True)
    restore.add_argument("--replace", action="store_true")
    commands.add_parser("restore-status", help="Show disposable PITR state")
    cleanup = commands.add_parser(
        "cleanup-restore", help="Delete only the disposable PITR container and volume"
    )
    cleanup.add_argument("--confirm", required=True)
    return result


def main(arguments: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(arguments)
    selected = [app for app in apps.REPLICATED_DATABASES if args.app is None or app.name == args.app]
    try:
        if args.command in ('restore', 'restore-status', 'cleanup-restore') and len(selected) != 1:
            raise BackupError('Disposable restore operations require an explicit --app')
        tools = [TodoBackup(app=app) for app in selected]
        # Validate the whole requested group before the first backup/restore-point write.
        if args.command in ('create', 'mark'):
            for tool in tools:
                tool.require_writable_primary()
                if tool.archive_status().split('|', 1)[0] != 'on':
                    raise BackupError(f'{tool.app.name}: archive_mode is not on')
        for tool in tools:
            prefix = tool.app.name + ': '
            if args.command == 'status':
                print(prefix + "\n".join(tool.status_lines()))
            elif args.command == 'create':
                print(prefix + f"Verified base backup: {tool.create_backup()}")
            elif args.command == 'mark':
                lsn = tool.create_restore_point(args.name)
                print(prefix + f"Archived restore point {args.name} at {lsn}")
            elif args.command == 'restore':
                tool.restore(args.backup, args.target, args.replace)
                print(prefix + f"PITR paused at {args.target}. Live database was not modified.")
            elif args.command == 'restore-status':
                print(prefix + f"recovery|paused|read_only = {tool.restore_status()}")
            elif args.command == 'cleanup-restore':
                tool.cleanup_restore(args.confirm)
                print(prefix + "Disposable PITR container and volume removed.")
        return 0
    except BackupError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
