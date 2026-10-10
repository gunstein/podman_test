"""Nightly base backups of every installed database, and restoring the latest one.

A single host installed with install.sh gets them without becoming a DR
host: install() writes platform-backup.timer, which runs

  python3 -m app_installer backup nightly --keep-days 7

every night. Each database's backup volume is already mounted in its pod at
BACKUP_DIRECTORY, so everything runs inside the database container, over its
local socket: pg_basebackup writes a self-contained copy (its WAL included)
to base/<UTC time>, pg_verifybackup checks it against its manifest, and only
then does LATEST name it. Backups older than --keep-days are deleted, never
the latest. A restore puts the latest backup back: the data as it was last
night. There is no WAL archive on a single host, so nothing in between.

A standby does nothing: the primary takes the backups. On a DR primary,
app-ops configure-backup replaces the timer's service with app_backup.py,
which adds the WAL archive and PITR and uses create(), listing(), expired()
and delete() from here.
"""
import re
import shutil
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import quadlet, settings
from .commands import run

BACKUP_DIRECTORY = '/var/lib/postgresql/backup'
BASE = f'{BACKUP_DIRECTORY}/base'
LATEST = f'{BACKUP_DIRECTORY}/LATEST'
BACKUP_NAME = re.compile(r'base-[0-9]{8}T[0-9]{6}Z')
# The nightly run fails when less than this share of the disk under the home
# directory (Podman's volumes) is free, so a filling disk shows as a failed unit.
MIN_FREE_FRACTION = 0.10
UNIT = 'platform-backup'
# The same schedule as the DR timer (deploy/dr/systemd/platform-backup.timer), so a
# DR host that takes over the unit keeps it.
TIMER = """[Unit]
Description=Run the Todo backup every night

[Timer]
OnCalendar=*-*-* 02:30
RandomizedDelaySec=30min
# A host that was off at night takes the missed backup when it starts.
Persistent=true

[Install]
WantedBy=timers.target
"""


def utc_now():
    return datetime.now(timezone.utc)


def in_container(database, *command, timeout=settings.COMMAND_TIMEOUT, description=None):
    """Run command inside the database's own container; return its stdout."""
    return run('podman', 'exec', database.container, *command, timeout=timeout,
               description=description or f'{database.name}: {command[0]}').stdout.strip()


def in_recovery(database):
    """True if the database is a standby (in recovery), False if it is a primary."""
    answer = run('podman', 'exec', '--interactive', database.container, 'psql', '--no-psqlrc',
                 '--username', database.name, '--dbname', 'postgres', '--tuples-only', '--no-align',
                 input='SELECT pg_is_in_recovery();\n', description=f'{database.name}: role query').stdout.strip()
    if answer not in ('t', 'f'):
        raise RuntimeError(f'{database.name}: unexpected answer to pg_is_in_recovery(): {answer!r}')
    return answer == 't'


def create(database, clock=utc_now):
    """Take a base backup of the database, verify it, then mark it as the latest; return its name."""
    name = clock().strftime('base-%Y%m%dT%H%M%SZ')
    in_container(database, 'sh', '-ec', 'umask 077; mkdir -p "$1"', 'mkdir', BASE)
    in_container(database, 'pg_basebackup', f'--username={database.name}', f'--pgdata={BASE}/{name}',
                 '--format=plain', '--wal-method=stream', '--checkpoint=fast', '--manifest-checksums=SHA256',
                 timeout=settings.DATA_COPY_TIMEOUT, description=f'{database.name}: base backup')
    in_container(database, 'pg_verifybackup', f'{BASE}/{name}', timeout=settings.DATA_COPY_TIMEOUT,
                 description=f'{database.name}: base backup verification')
    in_container(database, 'sh', '-ec', 'printf "%s\\n" "$1" > "$2"', 'latest', name, LATEST,
                 description=f'{database.name}: latest backup marker')
    return name


def listing(database):
    """The latest verified backup and every backup's name: (latest, names)."""
    latest, *names = in_container(database, 'sh', '-ec', 'cat "$1"; ls "$2"', 'list', LATEST, BASE,
                                  description=f'{database.name}: backup listing').split()
    return latest, [name for name in names if BACKUP_NAME.fullmatch(name)]


def expired(names, latest, cutoff):
    """The backups to delete: older than cutoff (a backup name), never the latest verified one.

    Names sort by time (base-YYYYMMDDTHHMMSSZ), so comparing them compares
    when they were taken.
    """
    return [name for name in sorted(names) if name < cutoff and name != latest]


def cutoff(keep_days, clock=utc_now):
    """The name a backup taken keep_days ago would have; older ones expire."""
    return (clock() - timedelta(days=keep_days)).strftime('base-%Y%m%dT%H%M%SZ')


def delete(database, names):
    """Delete these backups."""
    if names:
        in_container(database, 'sh', '-ec', 'base=$1; shift; for name do rm -rf -- "$base/$name"; done',
                     'delete', BASE, *names,
                     description=f'{database.name}: expired backup deletion')


def prune(database, keep_days, clock=utc_now):
    """Delete the backups older than keep_days, never the latest; return their names."""
    latest, names = listing(database)
    if latest not in names:
        raise RuntimeError(f'{database.name}: the latest verified backup {latest!r} is missing; nothing was deleted')
    old = expired(names, latest, cutoff(keep_days, clock))
    delete(database, old)
    return old


def runtime(quadlet_dir=None):
    return Path(quadlet_dir or settings.QUADLET_DIR) / settings.KUBE_RUNTIME


def installed_databases(platform, quadlet_dir=None):
    """The databases of the platform this host runs: those whose Quadlet unit is installed."""
    return [database for database in platform.replicated_databases
            if (runtime(quadlet_dir) / database.unit).exists()]


def nightly(platform, keep_days, quadlet_dir=None, clock=utc_now, disk=None):
    """Back up and prune every installed database, then check the disk; return (lines, problems).

    A standby does nothing. disk replaces shutil.disk_usage(home) in tests.
    """
    databases = installed_databases(platform, quadlet_dir)
    if not databases:
        raise RuntimeError('No database is installed on this host.')
    roles = [in_recovery(database) for database in databases]
    if all(roles):
        return ['standby: nothing to back up; the primary takes the backups'], []
    if any(roles):
        raise RuntimeError('Some databases are standbys and some are not; nothing was backed up')
    lines = []
    for database in databases:
        name = create(database, clock)
        deleted = prune(database, keep_days, clock)
        lines.append(f'{database.name}: verified base backup {name}; deleted {len(deleted)} older than '
                     f'{keep_days} days' + (f": {', '.join(deleted)}" if deleted else ''))
    total, _used, free = disk or shutil.disk_usage(Path.home())
    percent = round(100 * free / total)
    if free < MIN_FREE_FRACTION * total:
        return lines, [f'only {percent}% of the disk is free ({free // 2**20} MiB); the backup wants '
                       f'{MIN_FREE_FRACTION:.0%}']
    return lines + [f'Disk: {percent}% free ({free // 2**20} MiB)'], []


RESTORE_SCRIPT = """
find /data -mindepth 1 -delete
cp -a "/backup/base/$1/." /data/
rm -f /data/standby.signal /data/recovery.signal
"""


def restore(platform, confirm_restore, quadlet_dir=None):
    """Put every installed database back to its latest backup; return {database: backup name}.

    Destructive: everything written since that backup is lost. The caller
    refuses a DR host first (install.require_single_host). confirm_restore
    must be this host's name. Every backup is found and checked before
    anything stops; then the whole stack stops, each data volume is
    replaced with its backup, and everything starts again. PostgreSQL
    replays the WAL inside the backup and comes up as a primary.
    """
    if confirm_restore != socket.gethostname():
        raise ValueError(f'--confirm-restore must be exactly this host\'s name, {socket.gethostname()!r}')
    databases = installed_databases(platform, quadlet_dir)
    if not databases:
        raise RuntimeError('No database is installed on this host.')
    chosen = {}
    for database in databases:
        name = run('podman', 'run', '--rm', '--user', 'postgres', '--volume',
                   f"{database.volume('backup')}:/backup:ro,z", '--entrypoint', '/bin/sh', database.image,
                   '-ec', 'name=$(cat /backup/LATEST); test -s "/backup/base/$name/PG_VERSION"; echo "$name"',
                   description=f'{database.name}: latest backup check').stdout.strip()
        if not BACKUP_NAME.fullmatch(name):
            raise RuntimeError(f'{database.name}: no verified backup to restore; nothing was changed')
        chosen[database] = name
    # Every service in stop order (nginx first, the databases last), but not those of an app not installed here.
    absent = {pod for app in platform.apps if not (runtime(quadlet_dir) / app.unit).exists()
              for pod in (app.pod, app.database.container)}
    services = [workload.service for workload in reversed(platform.workloads()) if workload.pod not in absent]
    run('systemctl', '--user', 'stop', *services, allowed=(0, 5), timeout=settings.COMMAND_TIMEOUT)
    for database, name in chosen.items():
        run('podman', 'run', '--rm', '--user', 'postgres', '--security-opt', 'no-new-privileges',
            '--cap-drop', 'all', '--volume', f"{database.volume('data')}:/data:z",
            '--volume', f"{database.volume('backup')}:/backup:ro,z", '--entrypoint', '/bin/sh', database.image,
            '-ec', RESTORE_SCRIPT, 'restore', name, timeout=settings.DATA_COPY_TIMEOUT,
            description=f'{database.name}: restore of {name}')
    for database in chosen:
        quadlet.systemctl('start', database.service)
        run('podman', 'wait', '--condition=healthy', database.container, timeout=settings.HEALTH_TIMEOUT)
    for service in reversed(services):
        quadlet.systemctl('start', service)
    return {database.name: name for database, name in chosen.items()}


def service_unit(installer_directory):
    """platform-backup.service for a single host: the installer in installer_directory runs the nightly backup."""
    return f"""# The nightly backup of a single host: a verified base backup
# of every database, keeping the last 7 days. A failure fails the unit:
#   journalctl --user -u {UNIT}.service
# install.sh writes it; app-ops configure-backup replaces it on a DR primary.
[Unit]
Description=Todo nightly base backup and pruning

[Service]
Type=oneshot
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONPATH={installer_directory}
ExecStart=/usr/bin/python3 -m app_installer backup nightly --keep-days 7
TimeoutStartSec=3h
"""


def install_timer(installer_directory, unit_directory=None):
    """Write platform-backup.service and .timer as user units and turn the timer on; True if anything changed."""
    directory = Path(unit_directory or settings.SYSTEMD_USER_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    changed = quadlet.write(directory / f'{UNIT}.service', service_unit(installer_directory).encode(), 0o644)
    changed = quadlet.write(directory / f'{UNIT}.timer', TIMER.encode(), 0o644) or changed
    if changed:
        quadlet.systemctl('daemon-reload')
    timer = f'{UNIT}.timer'
    enabled = run('systemctl', '--user', 'is-enabled', timer, allowed=(0, 1)).stdout.strip() == 'enabled'
    active = run('systemctl', '--user', 'is-active', timer, allowed=(0, 3, 4)).stdout.strip() == 'active'
    if not (enabled and active):
        run('systemctl', '--user', 'enable', '--now', timer)
        changed = True
    return changed


def remove_timer(unit_directory=None):
    """Turn the timer off and remove both units; True if they were there."""
    directory = Path(unit_directory or settings.SYSTEMD_USER_DIR)
    paths = [directory / f'{UNIT}.timer', directory / f'{UNIT}.service']
    if not any(path.exists() for path in paths):
        return False
    run('systemctl', '--user', 'disable', '--now', f'{UNIT}.timer', allowed=(0, 1, 5))
    for path in paths:
        path.unlink(missing_ok=True)
    quadlet.systemctl('daemon-reload')
    return True
