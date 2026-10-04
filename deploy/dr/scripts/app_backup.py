"""Independent physical PostgreSQL backups and scoped disposable PITR per database.

Installed as /opt/todo/bin/app_backup.py on the current primary and run there:

  app_backup.py configure              turn on WAL archiving for every database
  app_backup.py status | create        archive status, or a verified base backup
  app_backup.py nightly --keep-days N  create, then delete backups older than N
                                       days and the WAL only they needed
                                       (todo-backup.timer; nothing on a standby)
  app_backup.py mark --name N          a named restore point, archived at once
  app_backup.py --app A restore ...    point-in-time restore into a throwaway
                                       container with no network: to a named
                                       restore point (--backup B --target N), or
                                       to a time (--target-time T, the base
                                       backup chosen unless --backup)
  app_backup.py --app A restore-status | cleanup-restore --confirm ...

Without --app, status, create and mark act on every database. Backups and
WAL live in each database's own backup volume on the same host: this
protects against mistakes and bad data, not against losing the VM.
A restore never touches the live database.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

# This DR tool reuses the installer (app_installer) and the DR building
# blocks (app_dr_host). app-ops installs it in /opt/todo/bin and both
# packages side by side in /opt/todo/lib, so they are found in lib next to
# bin. In a checkout, set PYTHONPATH=deploy/installer:deploy/dr instead.
# deploy/dr/README.md ("Where DR finds the installer") has the whole rule.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from app_dr_host import replication  # noqa: E402
from app_installer import apps, keycloak, settings, stack, target_render  # noqa: E402
from app_installer import backup as backups  # noqa: E402
from app_installer.commands import CommandError, run  # noqa: E402

DATA_DIRECTORY = "/var/lib/postgresql/data"
BACKUP_DIRECTORY = "/var/lib/postgresql/backup"
WAL_ARCHIVE = f"{BACKUP_DIRECTORY}/wal"
# PostgreSQL runs this with /bin/sh for each finished WAL file (%p, archived
# as %f) and may delete the original once it exits 0, so the copy must be on
# disk by then: copy to a temporary name, sync it, rename it into place and
# sync the directory, so a power loss leaves either no file or a whole one.
# A file already archived is accepted only if it is identical, never
# overwritten. psql returns settings separated by "|", so the command must
# not contain that character. configure() applies a changed command with a
# reload, not a restart.
ARCHIVE_COMMAND = (
    f'if test -f {WAL_ARCHIVE}/%f; then test "$(sha256sum < %p)" = "$(sha256sum < {WAL_ARCHIVE}/%f)"; '
    f"else cp %p {WAL_ARCHIVE}/%f.tmp && sync {WAL_ARCHIVE}/%f.tmp && mv {WAL_ARCHIVE}/%f.tmp {WAL_ARCHIVE}/%f "
    f"&& sync {WAL_ARCHIVE}; fi"
)
# One hour caps time-driven growth near 384 MiB/day; mark and configure force a switch.
ARCHIVE_TIMEOUT = "1h"
BACKUP_DIRECTORIES_SCRIPT = """
changed=false
for path in /backup /backup/base /backup/wal; do
    if [ ! -d "$path" ] || [ "$(stat -c %a "$path")" != 700 ]; then changed=true; fi
done
umask 077
mkdir -p /backup/base /backup/wal
chmod 0700 /backup /backup/base /backup/wal
if $changed; then echo changed; else echo unchanged; fi
"""
BACKUP_NAME = backups.BACKUP_NAME
RESTORE_POINT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}")
WAL_SEGMENT = re.compile(r"[0-9A-F]{24}")
# The line of a base backup's backup_label that names the first WAL file it needs.
START_WAL = re.compile(r"^START WAL LOCATION: \S+ \(file ([0-9A-F]{24})\)$", re.M)


def parse_target_time(text: str, now: datetime) -> datetime:
    """A restore target time in ISO 8601 with its UTC offset, returned in UTC.

    For example 2026-10-04T14:36:00+02:00, or 2026-10-04T12:36:00Z. A time
    without an offset is refused: it would name a different moment depending
    on the host's time zone. So is one that is not in the past.
    """
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise BackupError(f"Not an ISO 8601 time: {text!r} (for example 2026-10-04T14:36:00+02:00)") from None
    if moment.tzinfo is None:
        raise BackupError(f"The target time {text!r} needs its UTC offset, such as +02:00 or Z")
    moment = moment.astimezone(timezone.utc)
    if moment >= now:
        raise BackupError(f"The target time {moment:%Y-%m-%d %H:%M:%S} UTC is not in the past")
    return moment


class BackupError(RuntimeError):
    """A check of this tool's own refused, or configure() failed; a failed command is a CommandError."""


# Most commands here answer within seconds; the copies pass longer limits.
TIMEOUT = 30


class DatabaseBackup:
    """Backup, archiving and disposable restore for one database.

    Commands run through commands.run and SQL through replication.sql(), like
    every DR tool. clock, sleeper and monotonic replace the time of day,
    sleeping and the wait deadline clock in tests.
    """

    def __init__(
        self,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleeper: Callable[[float], None] = time.sleep,
        *, database: stack.Database = apps.SHARED_RESOURCE_OWNER.database,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.database = database
        self.image = database.image
        self.backup_volume = database.volume('backup')
        self.restore_volume = database.volume('restore-data')
        self.restore_container = database.names.resource('postgres-restore')
        self.clock = clock
        self.sleeper = sleeper
        self.monotonic = monotonic

    def _run(self, arguments: Sequence[str], description: str, timeout: float = TIMEOUT) -> str:
        """commands.run with this tool's default limit; returns stdout."""
        return run(*arguments, description=description, timeout=timeout).stdout.strip()

    def _sql(self, sql: str, description: str, *, container: Optional[str] = None,
             timeout: float = TIMEOUT) -> str:
        """replication.sql() in the live database, or in container (the disposable restore)."""
        return replication.sql(self.database, sql, container=container, description=description,
                               timeout=timeout)

    def _exists(self, kind: str, name: str) -> bool:
        """podman <kind> exists <name>; any answer other than yes or no is an error."""
        return run("podman", kind, "exists", name, allowed=(0, 1), timeout=TIMEOUT,
                   description=f"Podman {kind} {name} inspection").returncode == 0

    def database_state(self) -> tuple[bool, bool]:
        """Return (in recovery, read-only) for the live database."""
        output = self._sql("SELECT pg_is_in_recovery(), current_setting('transaction_read_only');",
                           "Live PostgreSQL role check")
        if output not in ("t|on", "f|off"):
            raise BackupError(f"Unexpected live PostgreSQL role: {output!r}")
        recovery, read_only = output.split("|")
        return recovery == "t", read_only == "on"

    def require_writable_primary(self) -> None:
        """Raise unless the live database is a writable primary; backups are only taken there."""
        recovery, read_only = self.database_state()
        if recovery or read_only:
            raise BackupError("Live PostgreSQL is not a writable promoted primary")

    def archive_status(self) -> str:
        """Raw archive_mode, last archived and failed WAL, timeout and counters, '|'-separated."""
        return self._sql(
            "SELECT current_setting('archive_mode'), "
            "COALESCE(last_archived_wal, ''), "
            "COALESCE(last_failed_wal, ''), "
            "current_setting('archive_timeout'), archived_count, failed_count "
            "FROM pg_stat_archiver;",
            "WAL archive status query",
        )

    def status_lines(self) -> list[str]:
        """Human-readable role and WAL archive status; read-only."""
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
        """Take a verified base backup of a primary that archives its WAL, and return its name.

        The backup itself is the single host's (app_installer.backup.create):
        pg_basebackup inside the database container, pg_verifybackup, then
        LATEST.
        """
        self.require_writable_primary()
        if self.archive_status().split("|", 1)[0] != "on":
            raise BackupError("archive_mode is not on")
        return backups.create(self.database, self.clock)

    def prune(self, keep_days: int) -> list[str]:
        """Delete the base backups older than keep_days, then the WAL only they needed; return their names.

        The backups go as on a single host (app_installer.backup): never the
        latest. pg_archivecleanup, which ships with PostgreSQL, then deletes
        every archived WAL file older than the first one the oldest kept
        backup needs, so every kept backup can still be restored to any point
        after it. Everything is read and checked before the first deletion.
        """
        latest, names = backups.listing(self.database)
        if latest not in names:
            raise BackupError(f"The latest verified backup {latest!r} is missing; nothing was deleted")
        old = backups.expired(names, latest, backups.cutoff(keep_days, self.clock))
        oldest = [name for name in names if name not in old][0]
        label = backups.in_container(self.database, "cat", f"{backups.BASE}/{oldest}/backup_label",
                                     description=f"{oldest} backup label read")
        start = START_WAL.search(label)
        if not start:
            raise BackupError(f"{oldest} has no readable START WAL LOCATION; nothing was deleted")
        backups.delete(self.database, old)
        backups.in_container(self.database, "pg_archivecleanup", WAL_ARCHIVE, start.group(1),
                             description=f"{self.database.name}: archived WAL cleanup")
        return old

    def _archive_settings(self) -> str:
        """The current archive_mode, archive_command and archive_timeout, '|'-separated."""
        return self._sql(
            "SELECT current_setting('archive_mode'), current_setting('archive_command'), "
            "current_setting('archive_timeout');",
            "Archive settings query",
        )

    def require_archive_prerequisites(self) -> None:
        """Read-only gates; the group checks all of them before its first write."""
        service = self.database.service
        # is-active exits 3 for an inactive or failed unit and 4 for an unknown one.
        active = run("systemctl", "--user", "is-active", service, allowed=(0, 3, 4), timeout=TIMEOUT,
                     description=f"{service} state query").stdout.strip()
        if active != "active":
            raise BackupError(f"{service} is not active")
        self.require_writable_primary()
        if not self._exists("secret", self.database.secret("replicator")):
            raise BackupError(f"Replication credential {self.database.secret('replicator')} is missing")
        if not self._exists("volume", self.backup_volume):
            raise BackupError(f"Backup volume {self.backup_volume} created by the PostgreSQL PVC is missing")
        try:
            mounts = json.loads(self._run(
                ["podman", "inspect", "--format", "{{json .Mounts}}", self.database.container],
                "PostgreSQL mount inspection",
            ))
        except ValueError as error:
            raise BackupError("PostgreSQL mount inspection returned invalid JSON") from error
        backup_mounts = [mount for mount in mounts if mount.get("Type") == "volume"
                         and mount.get("Name") == self.backup_volume
                         and mount.get("Destination") == BACKUP_DIRECTORY and mount.get("RW") is True]
        if len(backup_mounts) != 1:
            raise BackupError("The active Kube PostgreSQL workload must mount its writable backup PVC "
                              f"{self.backup_volume} at {BACKUP_DIRECTORY}")
        source = self._run(
            ["systemctl", "--user", "show", service, "--property=SourcePath", "--value"],
            "PostgreSQL service source query",
        )
        if not source.endswith(f"/{settings.KUBE_RUNTIME}/{self.database.unit}"):
            raise BackupError("Backup configuration refuses to replace a non-Kube PostgreSQL runtime")

    def prepare_archive(self) -> tuple[bool, bool, bool, bool]:
        """Returns (replication access changed, backup directories changed, settings changed, restart needed).

        Only turning archive_mode on needs a restart. A new archive_command or
        archive_timeout takes effect on a reload, so updating the command on a
        host that already archives restarts nothing.
        """
        access = replication.refresh_hba(self.database)
        directories = self._run(
            [
                "podman", "run", "--rm", "--user", "postgres",
                "--security-opt", "no-new-privileges", "--cap-drop", "all",
                "--volume", f"{self.backup_volume}:/backup:U,z",
                "--entrypoint", "/bin/sh", self.image, "-ec", BACKUP_DIRECTORIES_SCRIPT,
            ],
            "Private base-backup and WAL directory initialization",
        )
        current = self._archive_settings()
        if current == f"on|{ARCHIVE_COMMAND}|{ARCHIVE_TIMEOUT}":
            return access, directories == "changed", False, False
        self._sql(
            "ALTER SYSTEM SET archive_mode = 'on';\n"
            f"ALTER SYSTEM SET archive_command = '{ARCHIVE_COMMAND}';\n"
            f"ALTER SYSTEM SET archive_timeout = '{ARCHIVE_TIMEOUT}';",
            "Continuous WAL archiving configuration",
        )
        restart = current.split("|", 1)[0] != "on"
        if not restart:
            self._sql("SELECT pg_reload_conf();", "PostgreSQL configuration reload")
        return access, directories == "changed", True, restart

    def require_configured_archive(self) -> None:
        """After a restart or reload: wait until healthy, then raise unless the archive settings hold.

        A reload is applied a moment after pg_reload_conf() returns, so the
        settings are read again for up to 30 seconds.
        """
        self._run(["podman", "wait", "--condition=healthy", self.database.container],
                  "PostgreSQL health wait", timeout=settings.HEALTH_TIMEOUT)
        self.require_writable_primary()
        deadline = self.monotonic() + 30
        while self._archive_settings() != f"on|{ARCHIVE_COMMAND}|{ARCHIVE_TIMEOUT}":
            if self.monotonic() >= deadline:
                raise BackupError("PostgreSQL did not keep the configured archive settings")
            self.sleeper(1)

    def create_restore_point(self, name: str) -> str:
        """Create a named restore point, wait until its WAL is archived, and return its LSN.

        Switching to a new WAL file makes the restore point archivable at
        once, instead of after archive_timeout.
        """
        self.require_writable_primary()
        self._validate_restore_point(name)
        output = self._sql(f"SELECT pg_create_restore_point('{name}');", "Named restore point creation")
        self._switch_and_archive()
        return output

    def archive_current_wal(self) -> None:
        """Make every change made so far reach the archive, and wait for it.

        Without this, the last changes can wait for archive_timeout (an hour)
        before they reach the archive, and a restore cannot replay them. The
        restore point it writes first gives a quiet database a WAL record to
        switch away from; a restore point is only a marker.
        """
        self._sql("SELECT pg_create_restore_point('archive_before_restore');", "Restore point before a restore")
        self._switch_and_archive()

    def _switch_and_archive(self) -> None:
        """Switch to a new WAL file and wait until the one before it is archived."""
        wal = self._sql("SELECT pg_walfile_name(pg_current_wal_lsn());", "Current WAL segment query")
        self._sql("SELECT pg_switch_wal();", "WAL switch")
        self._wait_for_archived_wal(wal)

    def backup_before(self, moment: datetime) -> str:
        """The newest base backup taken before moment (UTC); names sort by time."""
        _latest, names = backups.listing(self.database)
        older = [name for name in names if name < moment.strftime("base-%Y%m%dT%H%M%SZ")]
        if not older:
            raise BackupError(f"No base backup was taken before {moment:%Y-%m-%d %H:%M:%S} UTC")
        return max(older)

    def _wait_for_archived_wal(self, wal: str) -> None:
        """Wait up to 30 seconds for the WAL file to appear in the backup volume."""
        if not WAL_SEGMENT.fullmatch(wal):
            raise BackupError(f"Unexpected WAL segment name: {wal!r}")
        archive_path = f"/var/lib/postgresql/backup/wal/{wal}"
        deadline = self.monotonic() + 30
        while self.monotonic() < deadline:
            limit = max(1.0, deadline - self.monotonic())
            found = run("podman", "exec", self.database.container, "test", "-f", archive_path,
                        allowed=(0, 1), timeout=limit, description="WAL archive inspection")
            if found.returncode == 0:
                return
            self.sleeper(1)
        raise BackupError(f"WAL segment was not archived within 30 seconds: {wal}")

    def restore(self, backup: Optional[str], target: Optional[str], replace: bool,
                target_time: Optional[str] = None) -> str:
        """Restore a base backup up to a named restore point or a time, in a throwaway container; return the backup.

        Give either target (a restore point made with mark) or target_time
        (parse_target_time). For a time, backup may be None: the newest base
        backup taken before it is used, and the live database first archives
        its current WAL, so the last changes are in the archive too.

        The copy goes into a new restore volume, and PostgreSQL starts there
        with no network, archiving off and no link to a primary. It replays
        archived WAL, then pauses at the target so the data can be read. The
        live data and the backups are only read (for a time, the live
        database only writes a restore point and switches WAL). Existing
        restore state is replaced only with replace=True.
        """
        if (target is None) == (target_time is None):
            raise BackupError("Give either a named restore point or a target time")
        if target is not None:
            if backup is None:
                raise BackupError("A named restore point needs --backup: the base backup taken before it")
            self._validate_restore_point(target)
            recovery_target = f"recovery_target_name={target}"
        else:
            moment = parse_target_time(target_time or "", self.clock())
            self.require_writable_primary()
            backup = backup or self.backup_before(moment)
            if backup >= moment.strftime("base-%Y%m%dT%H%M%SZ"):
                raise BackupError(f"{backup} was taken after the target time; choose an older one")
            self.archive_current_wal()
            recovery_target = f"recovery_target_time={moment:%Y-%m-%d %H:%M:%S}+00"
        self._validate_backup_name(backup)
        self._run(
            [
                "podman", "run", "--rm",
                "--user", "postgres",
                "--volume", f"{self.backup_volume}:/backup:ro,z",
                "--entrypoint", "/bin/sh",
                self.image,
                "-ec", 'test -s "/backup/base/$1/PG_VERSION"',
                self.database.names.resource("backup"), backup,
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
                    self.database.names.resource("backup"), backup,
                ],
                "Base backup copy into disposable restore volume",
                timeout=settings.DATA_COPY_TIMEOUT,
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
                    "-c", recovery_target,
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
            return backup
        except Exception as error:
            if self._remove_restore_container():
                raise
            raise BackupError(
                f"{error}; removing {self.restore_container} afterwards failed too, "
                f"remove it with: podman rm --force {self.restore_container}"
            ) from error

    def _remove_restore_container(self) -> bool:
        """Remove the restore container after a failed start; False, never an exception, if that fails.

        The caller then reports why the start failed, not why the cleanup did.
        """
        try:
            run("podman", "rm", "--force", self.restore_container, timeout=TIMEOUT)
        except CommandError:
            return False
        return True

    def _wait_for_restore_pause(self) -> None:
        """Wait up to 60 seconds for the restore to pause at its target: one deadline for every query."""
        deadline = self.monotonic() + 60
        while self.monotonic() < deadline:
            limit = max(1.0, deadline - self.monotonic())
            # psql exits 2 when it cannot connect, as while PostgreSQL still starts;
            # keep waiting then. Any other failure, and a hang, is an error at once.
            if replication.sql(self.database, "SELECT pg_is_in_recovery(), pg_is_wal_replay_paused();",
                               container=self.restore_container, timeout=limit, allowed=(0, 2),
                               description="Disposable PITR status query") == "t|t":
                return
            if not self._exists("container", self.restore_container):
                raise BackupError("Disposable PITR container stopped during recovery")
            self.sleeper(1)
        raise BackupError("PITR did not reach its target within 60 seconds; recovery cannot reach a target "
                          "after the last archived change or before the end of the base backup")

    def restore_status(self) -> str:
        """'recovery|paused|read_only' for the restore container; 't|t|on' means paused at the target."""
        if not self._exists("container", self.restore_container):
            raise BackupError("Disposable PITR container does not exist")
        return self._sql(
            "SELECT pg_is_in_recovery(), pg_is_wal_replay_paused(), current_setting('transaction_read_only');",
            "Disposable PITR status query", container=self.restore_container)

    def cleanup_restore(self, confirmation: str) -> None:
        """Delete the restore container and volume; confirmation must be the container name."""
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


def configure(tools: Sequence[DatabaseBackup], journal: Path) -> dict:
    """Turn on WAL archiving for the complete group, restarting the app tier at most once.

    Every database is checked before the first change. Databases that did
    not archive yet are restarted together, with the application tier
    stopped meanwhile; a database that only gets a new archive command is
    reloaded. Each changed database then archives a restore point, which
    proves archiving works end to end with the command now in place.
    """
    try:
        replication.require_promoted_group(journal)
        for tool in tools:
            tool.require_archive_prerequisites()
        prepared = [(tool, *tool.prepare_archive()) for tool in tools]
        restart = [tool for tool, _access, _directories, _changed, needed in prepared if needed]
        if restart:
            for service in apps.services(databases=False):
                # Exit 5 means the unit is not loaded, which is as good as stopped.
                run("systemctl", "--user", "stop", service, allowed=(0, 5),
                    description=f"Stopping {service} before the PostgreSQL restart")
        tools[0]._run(["systemctl", "--user", "daemon-reload"], "User systemd reload")
        for tool in restart:
            tool._run(["systemctl", "--user", "restart", tool.database.service],
                      f"{tool.database.service} restart", timeout=settings.COMMAND_TIMEOUT)
        for tool in tools:
            tool.require_configured_archive()
        tools[0]._run(["systemctl", "--user", "start", "shared-proxy.service"],
                      "Application tier start", timeout=settings.COMMAND_TIMEOUT)
        # Each app through nginx on the public hostname this host serves (its
        # record), else the default the bundle was built with.
        served = target_render.hostnames(target_render.read_record())
        for app in apps.APPS:
            keycloak.wait("/ready", 30, 1, "ready", hostname=served.get(app.name, app.hostname))
        keycloak.wait("/auth/realms/todo/.well-known/openid-configuration", 90, 2)
        verified = {}
        for tool, _access, directories, changed, _needed in prepared:
            if directories or changed:
                point = f"{tool.database.role('archive_check')}_{tool.clock():%Y%m%d%H%M%S%f}"
                tool.create_restore_point(point)
                verified[tool.database.name] = point
    except BackupError:
        raise
    except RuntimeError as error:
        raise BackupError(str(error)) from error
    return {"changed": any(any(flags) for _tool, *flags in prepared),
            "restarted": [tool.database.name for tool in restart], "verified": verified}


def require_backups_possible(tools: Sequence[DatabaseBackup]) -> None:
    """Raise unless every database is a writable primary that archives its WAL; checked before the first backup."""
    for tool in tools:
        tool.require_writable_primary()
        if tool.archive_status().split('|', 1)[0] != 'on':
            raise BackupError(f'{tool.database.name}: archive_mode is not on')


def nightly(tools: Sequence[DatabaseBackup], keep_days: int) -> list[str]:
    """The nightly backup (todo-backup.timer): a verified base backup of every database, then pruning.

    Returns what it did. On a standby group it does nothing: the primary
    takes the backups (backups on the standby are backlog D2). A group that is neither, or a primary that does not archive,
    fails before the first backup.
    """
    if all(tool.database_state()[0] for tool in tools):
        return ['standby: nothing to back up; the primary takes the backups']
    require_backups_possible(tools)
    lines = []
    for tool in tools:
        name = tool.create_backup()
        deleted = tool.prune(keep_days)
        lines.append(f"{tool.database.name}: verified base backup {name}; deleted {len(deleted)} older than "
                     f"{keep_days} days" + (f": {', '.join(deleted)}" if deleted else ''))
    return lines


def parser() -> argparse.ArgumentParser:
    """Command-line arguments; see the module docstring."""
    result = argparse.ArgumentParser(
        description="Manage independent application backups and disposable PITR."
    )
    result.add_argument('--app', choices=[d.name for d in apps.REPLICATED_DATABASES],
                        help='Select one database; status/create/mark default to the whole group')
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show live database and WAL archive status")
    commands.add_parser("create", help="Create and verify a physical base backup")
    scheduled = commands.add_parser("nightly", help="Create a backup, then delete backups older than --keep-days")
    scheduled.add_argument("--keep-days", type=int, required=True)
    mark = commands.add_parser("mark", help="Create and archive a named restore point")
    mark.add_argument("--name", required=True)
    restore = commands.add_parser(
        "restore", help="Restore into an isolated disposable PostgreSQL container"
    )
    restore.add_argument("--backup", help="the base backup; with --target-time, the newest before it by default")
    target = restore.add_mutually_exclusive_group(required=True)
    target.add_argument("--target", help="a restore point made with mark")
    target.add_argument("--target-time", help="a time with its UTC offset, e.g. 2026-10-04T14:36:00+02:00")
    restore.add_argument("--replace", action="store_true")
    commands.add_parser("restore-status", help="Show disposable PITR state")
    configuration = commands.add_parser(
        "configure", help="Configure and verify continuous WAL archiving for the whole group"
    )
    configuration.add_argument("--journal", type=Path,
                               default=Path.home() / settings.DR_CONFIG / settings.PROMOTION_RECORD)
    cleanup = commands.add_parser(
        "cleanup-restore", help="Delete only the disposable PITR container and volume"
    )
    cleanup.add_argument("--confirm", required=True)
    return result


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """Run one command for the selected databases; print errors as 'ERROR: ...' and return 1."""
    args = parser().parse_args(arguments)
    selected = [database for database in apps.REPLICATED_DATABASES if args.app is None or database.name == args.app]
    try:
        if args.command in ('restore', 'restore-status', 'cleanup-restore') and len(selected) != 1:
            raise BackupError('Disposable restore operations require an explicit --app')
        if args.command == 'configure' and args.app is not None:
            raise BackupError('configure always covers the complete database group')
        tools = [DatabaseBackup(database=database) for database in selected]
        if args.command == 'configure':
            print(json.dumps(configure(tools, args.journal)))
            return 0
        if args.command == 'nightly':
            if args.app is not None or args.keep_days < 1:
                raise BackupError('nightly covers the complete database group and keeps at least one day')
            print("\n".join(nightly(tools, args.keep_days)))
            return 0
        # Validate the whole requested group before the first backup/restore-point write.
        if args.command in ('create', 'mark'):
            require_backups_possible(tools)
        for tool in tools:
            prefix = tool.database.name + ': '
            if args.command == 'status':
                print(prefix + "\n".join(tool.status_lines()))
            elif args.command == 'create':
                print(prefix + f"Verified base backup: {tool.create_backup()}")
            elif args.command == 'mark':
                lsn = tool.create_restore_point(args.name)
                print(prefix + f"Archived restore point {args.name} at {lsn}")
            elif args.command == 'restore':
                chosen = tool.restore(args.backup, args.target, args.replace, args.target_time)
                print(prefix + f"PITR from {chosen} paused at {args.target or args.target_time}. "
                      "Live database was not modified.")
            elif args.command == 'restore-status':
                print(prefix + f"recovery|paused|read_only = {tool.restore_status()}")
            elif args.command == 'cleanup-restore':
                tool.cleanup_restore(args.confirm)
                print(prefix + "Disposable PITR container and volume removed.")
        return 0
    except (BackupError, CommandError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
