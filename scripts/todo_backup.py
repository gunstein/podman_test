#!/usr/bin/env python3
"""Physical PostgreSQL backup and disposable PITR for the Todo demo."""

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence


IMAGE = "docker.io/library/postgres:17.11"
BACKUP_VOLUME = "todo-postgres-backup"
RESTORE_VOLUME = "todo-postgres-restore-data"
RESTORE_CONTAINER = "todo-postgres-restore"
DATA_DIRECTORY = "/var/lib/postgresql/data"
BACKUP_NAME = re.compile(r"base-[0-9]{8}T[0-9]{6}Z")
RESTORE_POINT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}")


class BackupError(RuntimeError):
    """An expected, operator-actionable backup error."""


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def run_command(arguments: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(arguments), check=False, capture_output=True, text=True
    )


class TodoBackup:
    def __init__(
        self,
        runner: Runner = run_command,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner
        self.clock = clock
        self.sleeper = sleeper

    def _run(self, arguments: Sequence[str], description: str) -> str:
        result = self.runner(arguments)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            suffix = f": {detail}" if detail else ""
            raise BackupError(f"{description} failed{suffix}")
        return result.stdout.strip()

    def _exists(self, kind: str, name: str) -> bool:
        result = self.runner(["podman", kind, "exists", name])
        if result.returncode not in (0, 1):
            detail = (result.stderr or result.stdout).strip()
            raise BackupError(f"Could not inspect Podman {kind} {name}: {detail}")
        return result.returncode == 0

    def database_state(self) -> tuple[bool, bool]:
        output = self._run(
            [
                "podman", "exec", "todo-postgres", "psql",
                "--username", "todo", "--dbname", "postgres",
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
                "podman", "exec", "todo-postgres", "psql",
                "--username", "todo", "--dbname", "postgres",
                "--tuples-only", "--no-align", "--field-separator=|",
                "--command",
                "SELECT current_setting('archive_mode'), "
                "COALESCE(last_archived_wal, ''), "
                "COALESCE(last_failed_wal, ''), "
                "archived_count, failed_count "
                "FROM pg_stat_archiver;",
            ],
            "WAL archive status query",
        )

    def status_lines(self) -> list[str]:
        recovery, read_only = self.database_state()
        archive = self.archive_status().split("|")
        if len(archive) != 5:
            raise BackupError(f"Unexpected WAL archive status: {'|'.join(archive)!r}")
        return [
            f"Database recovery mode: {'yes' if recovery else 'no'}",
            f"Database writable: {'no' if read_only else 'yes'}",
            f"Archive mode: {archive[0]}",
            f"Last archived WAL: {archive[1] or 'none'}",
            f"Last failed WAL: {archive[2] or 'none'}",
            f"Archived segments: {archive[3]}",
            f"Failed archive attempts: {archive[4]}",
            f"Backup volume: {BACKUP_VOLUME}",
        ]

    def create_backup(self) -> str:
        self.require_writable_primary()
        if self.archive_status().split("|", 1)[0] != "on":
            raise BackupError("archive_mode is not on")

        name = self.clock().strftime("base-%Y%m%dT%H%M%SZ")
        self._run(
            [
                "podman", "run", "--rm",
                "--network", "todo-network",
                "--user", "postgres",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "all",
                "--pids-limit", "128",
                "--volume", f"{BACKUP_VOLUME}:/backup:z",
                "--secret",
                "todo-replicator-password,type=env,target=PGPASSWORD",
                IMAGE,
                "pg_basebackup",
                "--host=todo-postgres",
                "--port=5432",
                "--username=todo_replicator",
                f"--pgdata=/backup/base/{name}",
                "--format=plain",
                "--wal-method=stream",
                "--checkpoint=fast",
                "--manifest-checksums=SHA256",
                "--progress",
            ],
            "Physical base backup",
        )
        self._run(
            [
                "podman", "run", "--rm",
                "--user", "postgres",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "all",
                "--volume", f"{BACKUP_VOLUME}:/backup:z",
                "--entrypoint", "pg_verifybackup",
                IMAGE,
                f"/backup/base/{name}",
            ],
            "Base backup verification",
        )
        self._run(
            [
                "podman", "run", "--rm",
                "--user", "postgres",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "all",
                "--volume", f"{BACKUP_VOLUME}:/backup:z",
                "--entrypoint", "/bin/sh",
                IMAGE,
                "-ec", 'printf "%s\\n" "$1" > /backup/LATEST',
                "todo-backup", name,
            ],
            "Latest backup marker update",
        )
        return name

    def create_restore_point(self, name: str) -> str:
        self.require_writable_primary()
        self._validate_restore_point(name)
        output = self._run(
            [
                "podman", "exec", "todo-postgres", "psql",
                "--username", "todo", "--dbname", "postgres",
                "--tuples-only", "--no-align",
                "--command",
                f"SELECT pg_create_restore_point('{name}');",
            ],
            "Named restore point creation",
        )
        wal = self._run(
            [
                "podman", "exec", "todo-postgres", "psql",
                "--username", "todo", "--dbname", "postgres",
                "--tuples-only", "--no-align",
                "--command", "SELECT pg_walfile_name(pg_current_wal_lsn());",
            ],
            "Current WAL segment query",
        )
        self._run(
            [
                "podman", "exec", "todo-postgres", "psql",
                "--username", "todo", "--dbname", "postgres",
                "--tuples-only", "--no-align",
                "--command", "SELECT pg_switch_wal();",
            ],
            "WAL switch after restore point",
        )
        self._wait_for_archived_wal(wal)
        return output

    def _wait_for_archived_wal(self, wal: str) -> None:
        for _attempt in range(30):
            fields = self.archive_status().split("|")
            if len(fields) == 5 and fields[1] == wal:
                return
            self.sleeper(1)
        raise BackupError(f"WAL segment was not archived within 30 seconds: {wal}")

    def restore(self, backup: str, target: str, replace: bool) -> None:
        self._validate_backup_name(backup)
        self._validate_restore_point(target)
        self._run(
            [
                "podman", "run", "--rm",
                "--user", "postgres",
                "--volume", f"{BACKUP_VOLUME}:/backup:ro,z",
                "--entrypoint", "/bin/sh",
                IMAGE,
                "-ec", 'test -s "/backup/base/$1/PG_VERSION"',
                "todo-backup", backup,
            ],
            "Selected base backup check",
        )

        container_exists = self._exists("container", RESTORE_CONTAINER)
        volume_exists = self._exists("volume", RESTORE_VOLUME)
        if (container_exists or volume_exists) and not replace:
            raise BackupError(
                "Disposable restore state already exists; rerun with --replace "
                "only after confirming it can be deleted"
            )
        if replace:
            if container_exists:
                self._run(
                    ["podman", "rm", "--force", RESTORE_CONTAINER],
                    "Old restore container removal",
                )
            if volume_exists:
                self._run(
                    ["podman", "volume", "rm", RESTORE_VOLUME],
                    "Old restore volume removal",
                )

        self._run(
            ["podman", "volume", "create", RESTORE_VOLUME],
            "Disposable restore volume creation",
        )
        try:
            self._run(
                [
                    "podman", "run", "--rm",
                    "--user", "postgres",
                    "--security-opt", "no-new-privileges",
                    "--cap-drop", "all",
                    "--volume", f"{BACKUP_VOLUME}:/backup:ro,z",
                    "--volume", f"{RESTORE_VOLUME}:/restore:U,Z",
                    "--entrypoint", "/bin/sh",
                    IMAGE,
                    "-ec",
                    'cp -a "/backup/base/$1/." /restore/; '
                    "rm -f /restore/standby.signal /restore/recovery.signal; "
                    "touch /restore/recovery.signal; chmod 0700 /restore",
                    "todo-backup", backup,
                ],
                "Base backup copy into disposable restore volume",
            )
            self._run(
                [
                    "podman", "run", "--detach",
                    "--name", RESTORE_CONTAINER,
                    "--network", "none",
                    "--user", "postgres",
                    "--security-opt", "no-new-privileges",
                    "--cap-drop", "all",
                    "--pids-limit", "128",
                    "--volume", f"{RESTORE_VOLUME}:{DATA_DIRECTORY}:Z",
                    "--volume",
                    f"{BACKUP_VOLUME}:/var/lib/postgresql/backup:ro,z",
                    "--entrypoint", "postgres",
                    IMAGE,
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
            self.runner(["podman", "rm", "--force", RESTORE_CONTAINER])
            raise

    def _wait_for_restore_pause(self) -> None:
        for _attempt in range(60):
            result = self.runner(
                [
                    "podman", "exec", RESTORE_CONTAINER,
                    "psql", "--username", "todo", "--dbname", "postgres",
                    "--tuples-only", "--no-align", "--field-separator=|",
                    "--command",
                    "SELECT pg_is_in_recovery(), pg_is_wal_replay_paused();",
                ]
            )
            if result.returncode == 0 and result.stdout.strip() == "t|t":
                return
            if not self._exists("container", RESTORE_CONTAINER):
                raise BackupError("Disposable PITR container stopped during recovery")
            self.sleeper(1)
        raise BackupError("PITR did not reach the named restore point within 60 seconds")

    def restore_status(self) -> str:
        if not self._exists("container", RESTORE_CONTAINER):
            raise BackupError("Disposable PITR container does not exist")
        return self._run(
            [
                "podman", "exec", RESTORE_CONTAINER,
                "psql", "--username", "todo", "--dbname", "postgres",
                "--tuples-only", "--no-align", "--field-separator=|",
                "--command",
                "SELECT pg_is_in_recovery(), pg_is_wal_replay_paused(), "
                "current_setting('transaction_read_only');",
            ],
            "Disposable PITR status query",
        )

    def cleanup_restore(self, confirmation: str) -> None:
        if confirmation != RESTORE_CONTAINER:
            raise BackupError(
                f"Cleanup confirmation must be exactly {RESTORE_CONTAINER!r}"
            )
        if self._exists("container", RESTORE_CONTAINER):
            self._run(
                ["podman", "rm", "--force", RESTORE_CONTAINER],
                "Disposable restore container removal",
            )
        if self._exists("volume", RESTORE_VOLUME):
            self._run(
                ["podman", "volume", "rm", RESTORE_VOLUME],
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
        description="Manage Todo physical backups and disposable PITR."
    )
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
    tool = TodoBackup()
    try:
        if args.command == "status":
            print("\n".join(tool.status_lines()))
        elif args.command == "create":
            print(f"Verified base backup: {tool.create_backup()}")
        elif args.command == "mark":
            lsn = tool.create_restore_point(args.name)
            print(f"Archived restore point {args.name} at {lsn}")
        elif args.command == "restore":
            tool.restore(args.backup, args.target, args.replace)
            print(
                f"PITR paused at {args.target}. Live database was not modified."
            )
        elif args.command == "restore-status":
            print(f"recovery|paused|read_only = {tool.restore_status()}")
        elif args.command == "cleanup-restore":
            tool.cleanup_restore(args.confirm)
            print("Disposable PITR container and volume removed.")
        return 0
    except BackupError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
