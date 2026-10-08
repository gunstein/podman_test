"""Local DR checks and guarded promotion of the complete database group.

Installed as /opt/todo/bin/app_dr.py on both DR hosts and run there:

  app_dr.py configure ...   write the DR settings (done by install-dr-tool)
  app_dr.py status          show each database's role, lag and primary reachability
  app_dr.py check           read-only: is replication, archiving, its TLS
                            certificates and disk space fine for this host's
                            role, and could this host take over?
                            (todo-dr-check.timer)
  app_dr.py renew-tls       renew each primary's replication certificate once
                            fewer than 30 days are left
                            (todo-replication-tls.timer)
  app_dr.py preflight ...   read-only: may the group be promoted now? (standby)
  app_dr.py promote ...     preflight, then promote every database (standby)

Promotion is all or nothing in intent, but cannot be undone. Every step is
written to a promotion record first, and a failed or partial promotion is
never retried automatically: a person must look at the record and at each
database first.
"""
import argparse
import fcntl
import json
import os
import shutil
import socket
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Callable, List, Optional, Sequence

# This DR tool reuses the installer (app_installer) and the DR building
# blocks (app_dr_host). app-ops installs it in /opt/todo/bin and both
# packages side by side in /opt/todo/lib, so they are found in lib next to
# bin. In a checkout, set PYTHONPATH=deploy/installer:deploy/dr instead.
# deploy/dr/README.md ("Where DR finds the installer") has the whole rule.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from app_dr_host import nginx_tls, replication, replication_tls, transfer  # noqa: E402
from app_installer import apps, quadlet, settings  # noqa: E402
from app_installer.commands import run  # noqa: E402

DEFAULT_CONFIG = Path.home() / settings.DR_CONFIG / 'todo-dr.json'
DEFAULT_JOURNAL = DEFAULT_CONFIG.with_name(settings.PROMOTION_RECORD)
# Status and promotion commands answer within seconds. Two minutes is
# generous, and keeps a hung command from stalling a failover.
TIMEOUT = 120
# The check fails when less than this share of the disk under the home
# directory (Podman's volumes, so the databases, their WAL and backups) is free.
MIN_FREE_FRACTION = 0.10


class DrError(RuntimeError):
    """A check of this tool's own refused: wrong confirmation, host, settings or state.

    A failed command raises commands.CommandError, and a broken replication
    rule (replication.require_standby) a RuntimeError that says which; main()
    prints all of them as "ERROR: ...".
    """


@dataclass(frozen=True)
class Config:
    """The DR settings written by 'configure': who the primary is, and where.

    revision and bundle are what the check compares this host with: the
    revision of the operations package that installed the tool, and where
    this host keeps its offline bundle ('' in settings written before them).
    """

    primary_name: str
    primary_address: str
    standby_name: str
    rpo_target_seconds: int
    applications: tuple = ()
    revision: str = ''
    bundle: str = ''


@dataclass(frozen=True)
class DatabaseStatus:
    """One database's role and replay position, as replication.status() reports it."""

    in_recovery: bool
    transaction_read_only: bool
    receive_lsn: str
    replay_lsn: str
    apply_lag_bytes: int


Connector = Callable[[str, int, float], bool]


def tcp_reachable(address: str, port: int, timeout: float) -> bool:
    """Return True if a TCP connection to address:port opens within timeout seconds."""
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def parse_config(raw: dict, source: Path) -> Config:
    """Check a loaded DR configuration and return it as a Config, or raise DrError.

    Also accepts the older key rpo_seconds for rpo_target_seconds.
    """
    try:
        names = raw.get('applications', [])
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError('applications must be a list of registered names')
        config = Config(str(raw['primary_name']), str(raw['primary_address']), str(raw['standby_name']),
                        int(raw['rpo_target_seconds'] if 'rpo_target_seconds' in raw else raw['rpo_seconds']), tuple(names),
                        str(raw.get('revision', '')), str(raw.get('bundle', '')))
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise DrError(f'Cannot read valid DR configuration from {source}: {error}') from error
    if not all((config.primary_name, config.primary_address, config.standby_name)):
        raise DrError(f'DR configuration contains an empty host identity: {source}')
    if config.rpo_target_seconds <= 0:
        raise DrError('rpo_target_seconds must be greater than zero')
    return config


def load_config(path: Path) -> Config:
    """Read and check the DR configuration file."""
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise DrError(f'Cannot read valid DR configuration from {path}: {error}') from error
    return parse_config(raw, path)


def write_config(path: Path, primary_name: str, primary_address: str, standby_name: str,
                 rpo_target_seconds: int, revision: str = '', bundle: str = '') -> bool:
    """Write the private (0600) DR settings for the complete group; True if they changed.

    primary_address must be a literal IPv4 address, so the preflight's
    "primary still answers" check always tests that machine, never whatever a
    name happens to resolve to.
    """
    try:
        primary_address = replication.address(primary_address)
    except ValueError as error:
        raise DrError(f'primary_address must be a literal IPv4 address: {primary_address!r}') from error
    raw = {'applications': [database.name for database in apps.REPLICATED_DATABASES], 'primary_name': primary_name,
           'primary_address': primary_address, 'standby_name': standby_name,
           'rpo_target_seconds': rpo_target_seconds}
    # Only when given, so settings written without them stay byte for byte the same.
    raw.update({key: value for key, value in (('revision', revision), ('bundle', bundle)) if value})
    parse_config(raw, path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    return quadlet.write(path, json.dumps(raw).encode(), 0o600)


class StandbyGroup:
    """Status, preflight and promotion for every replicated database on this host.

    Commands run through commands.run and SQL through replication.sql(), like
    every DR tool; connector replaces the TCP check in tests. The
    configuration must list exactly the registered database group, in order,
    so a tool configured for an older group refuses to promote a newer one.
    """

    def __init__(self, config: Config, connector: Connector = tcp_reachable,
                 journal_path: Path = DEFAULT_JOURNAL):
        self.config, self.connector = config, connector
        self.databases = apps.REPLICATED_DATABASES
        expected = tuple(database.name for database in self.databases)
        if (config.applications and config.applications != expected) or (
                not config.applications and len(expected) != 1):
            raise DrError('DR configuration must explicitly match the complete ordered application registry')
        self.journal_path = Path(journal_path)

    def service_status(self, database=None):
        """systemctl --user is-active for the database service, e.g. 'active'."""
        database = database or self.databases[0]
        return run('systemctl', '--user', 'is-active', database.service, timeout=TIMEOUT,
                   description=f'{database.name}: PostgreSQL systemd status check').stdout.strip()

    def container_health(self, database=None):
        """The database container's health check result, e.g. 'healthy'."""
        database = database or self.databases[0]
        return run('podman', 'inspect', '--format', '{{.State.Health.Status}}', database.container,
                   timeout=TIMEOUT, description=f'{database.name}: PostgreSQL container health check').stdout.strip()

    @staticmethod
    def _query(database, statement):
        """replication.sql() with this tool's time limit."""
        return replication.sql(database, statement, timeout=TIMEOUT,
                               description=f'{database.name}: PostgreSQL recovery query')

    def database_status(self, database=None):
        """The database's role and replay position; raises RuntimeError if it cannot be read."""
        database = database or self.databases[0]
        return DatabaseStatus(**replication.status(database, query=self._query))

    def primary_reachable(self, database=None):
        """True if the configured primary still accepts TCP on this database's port."""
        database = database or self.databases[0]
        return self.connector(self.config.primary_address, database.replication_port, 2.0)

    def status_lines(self) -> List[str]:
        """Human-readable status for every database; read-only."""
        lines = []
        for database in self.databases:
            service, health = self.service_status(database), self.container_health(database)
            state = self.database_status(database)
            primary = 'reachable' if self.primary_reachable(database) else 'unreachable'
            lines += [f'Application: {database.name}', f'Service: {service}', f'Container: {health}',
                      f"Database role: {'standby' if state.in_recovery else 'promoted primary'}",
                      f"Writable: {'no' if state.transaction_read_only else 'yes'}",
                      f'Receive LSN: {state.receive_lsn or "not available"}',
                      f'Replay LSN: {state.replay_lsn or "not available"}',
                      f'Local apply lag: {state.apply_lag_bytes} bytes' + self._receive_note(state),
                      f'Primary endpoint {self.config.primary_address}:{database.replication_port}: {primary}']
        lines.append('Configured RPO target (informational): at most '
                     f'{self.config.rpo_target_seconds} seconds')
        if self.journal_path.exists():
            lines.append(f'Promotion decision record: {self.journal_path}')
        return lines

    @staticmethod
    def _receive_note(state):
        """Explain the one case where receive is behind replay: it is expected, not lag."""
        if state.receive_lsn and state.replay_lsn and (
                replication.lsn(state.receive_lsn) < replication.lsn(state.replay_lsn)):
            return ' (receive restarted at the WAL segment start after a walreceiver restart; nothing to replay)'
        return ''

    def preflight(self, fencing_confirmation):
        """Read-only check that promotion is safe now; returns each database's state.

        Refuses unless the operator confirms the primary is fenced, this is
        the configured standby, no earlier promotion record exists, and every
        database is healthy, read-only, fully replayed and cut off from the
        primary. A primary that still answers means fencing has not been
        shown, and promoting would risk two writable primaries.
        """
        expected = f'{self.config.primary_name} is fenced'
        if fencing_confirmation != expected:
            raise DrError(f'Fencing confirmation must be exactly: {expected!r}')
        local_hostname = socket.gethostname()
        if local_hostname != self.config.standby_name:
            raise DrError('Run promotion on the configured standby host '
                          f'{self.config.standby_name!r}, not {local_hostname!r}')
        if self.journal_path.exists():
            raise DrError('A promotion decision already exists. Inspect all database roles and '
                          f'{self.journal_path}; never blindly retry or remove the record.')
        states = {}
        # Validate the entire group before the first promotion command.
        for database in self.databases:
            if self.service_status(database) != 'active':
                raise DrError(f'{database.name}: PostgreSQL systemd service is not active')
            if self.container_health(database) != 'healthy':
                raise DrError(f'{database.name}: PostgreSQL container is not healthy')
            # The shared standby gate: read-only, both LSNs known, nothing left to replay.
            state = DatabaseStatus(**replication.require_standby(database, query=self._query))
            if self.primary_reachable(database):
                raise DrError('Primary PostgreSQL still answers at '
                              f'{self.config.primary_address}:{database.replication_port}; fencing is not demonstrated')
            states[database.name] = state
        return states

    @contextmanager
    def _promotion_lock(self):
        """Hold an exclusive lock so two promotions can never run at the same time."""
        self.journal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.journal_path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DrError('Another promotion operation holds the lock') from error
            yield
        finally:
            os.close(descriptor)

    def _record(self, decision):
        """Write the promotion record atomically and flush it to disk before returning."""
        descriptor, temporary = tempfile.mkstemp(dir=self.journal_path.parent, prefix='.promotion-')
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(decision, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.journal_path)
            directory = os.open(self.journal_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def promote(self, fencing_confirmation, promotion_confirmation):
        """Promote every database in the group, in order. Cannot be undone.

        promotion_confirmation must be this standby's hostname. The record is
        written before the first database is promoted and after each one, so
        a crash shows exactly which databases were promoted. Any failure
        marks the record failed and stops; it is never rolled back or
        retried.
        """
        if promotion_confirmation != self.config.standby_name:
            raise DrError('Promotion confirmation must equal the standby hostname: '
                          f'{self.config.standby_name!r}')
        with self._promotion_lock():
            states = self.preflight(fencing_confirmation)
            decision = {'state': 'promoting', 'config': asdict(self.config),
                        'applications': list(states), 'before': {name: asdict(state) for name, state in states.items()},
                        'completed': []}
            self._record(decision)  # Durable decision before any irreversible operation.
            try:
                for database in self.databases:
                    replication.promote(database, query=self._query, command=partial(
                        run, timeout=TIMEOUT, description=f'{database.name}: PostgreSQL promotion'))
                    decision['completed'].append(database.name)
                    self._record(decision)
                result = {database.name: self.database_status(database) for database in self.databases}
                if any(state.in_recovery or state.transaction_read_only for state in result.values()):
                    raise DrError('The entire application group is not writable after promotion')
                decision['state'] = 'complete'
                self._record(decision)
                return result
            except BaseException:
                # No rollback or automatic retry: some databases may already be promoted.
                decision['state'] = 'failed'
                self._record(decision)
                raise


def check(databases=apps.REPLICATED_DATABASES, disk=None):
    """The scheduled check (todo-dr-check.timer): what is fine, and what is wrong, for this host's role.

    Returns (lines, problems). Each database's role is read live, so the same
    check fits the primary and the standby, and still fits after a failover
    or a rebuild. A primary needs a standby streaming over TLS, slots that
    keep their WAL, healthy WAL archiving if archiving is on, and a
    replication certificate with at least replication_tls.ALERT_DAYS left
    (renew-tls replaces it at 30); a standby must receive WAL. The
    replication CA must have CA_ALERT_DAYS left on either host. The group
    must be all primary or all standby, and the disk must have
    MIN_FREE_FRACTION free. Every database is checked before anything is
    reported, so one run names every problem. disk replaces
    shutil.disk_usage(home) in tests.
    """
    lines, problems, roles = [], [], {}
    for database in databases:
        primary = False
        try:
            state = replication.status(database, query=StandbyGroup._query)
            if state['in_recovery']:
                replication.receiving(database)
                roles[database.name] = 'standby'
                lines.append(f'{database.name}: standby, receiving WAL from the primary')
            else:
                primary = True
                streams = replication.standby_streams(database)
                archive = 'WAL archive healthy' if replication.archiving(database) else 'WAL archiving off'
                roles[database.name] = 'primary'
                lines.append(f'{database.name}: primary, {streams} standby streaming over TLS, {archive}')
        except RuntimeError as error:  # replication's own checks and a failed command (CommandError)
            problems.append(str(error))
        # Checked even when streaming failed: an expired certificate is a likely reason.
        if primary:
            expiry(f'{database.name}: replication certificate', partial(replication_tls.server_days_left, database),
                   replication_tls.ALERT_DAYS, 'todo-replication-tls.timer has not renewed it', lines, problems)
    expiry('Replication CA', replication_tls.ca_days_left, replication_tls.CA_ALERT_DAYS,
           'replace it by hand before then (backlog U2)', lines, problems)
    if len(set(roles.values())) > 1:
        problems.append('the group is split: ' + ', '.join(f'{name} {role}' for name, role in roles.items()))
    total, _used, free = disk or shutil.disk_usage(Path.home())
    percent = round(100 * free / total)
    if free < MIN_FREE_FRACTION * total:
        problems.append(f'only {percent}% of the disk is free ({free // 2**20} MiB); '
                        f'the check wants {MIN_FREE_FRACTION:.0%}')
    else:
        lines.append(f'Disk: {percent}% free ({free // 2**20} MiB)')
    return lines, problems


def expiry(name, read_days, alert_days, advice, lines, problems):
    """Add how long a certificate lasts to lines, or a problem when fewer than alert_days are left."""
    try:
        days = read_days()
    except (RuntimeError, ValueError) as error:  # no certificate, or openssl could not read it
        problems.append(f'{name}: cannot read its expiry ({error})')
        return
    if days < alert_days:
        problems.append(f'{name} expires in {days} days; {advice}')
    else:
        lines.append(f'{name}: valid {days} more days')


def renew_tls(databases=apps.REPLICATED_DATABASES):
    """The nightly renewal (todo-replication-tls.timer): renew each primary's certificate if it is due.

    Returns (lines, problems), like check(). Each database's role is read
    live, so the same timer runs on both hosts: a standby has nothing to
    renew, and after a failover the promoted host renews its own
    certificates. Every database is tried, so one failure does not keep the
    others from being renewed.
    """
    lines, problems = [], []
    for database in databases:
        try:
            if replication.status(database, query=StandbyGroup._query)['in_recovery']:
                lines.append(f'{database.name}: standby, nothing to renew')
                continue
            renewed = replication_tls.renew(database)
            days = replication_tls.server_days_left(database)
            lines.append(f'{database.name}: ' + ('new replication certificate' if renewed
                                                 else 'replication certificate kept')
                         + f', valid {days} more days')
        except (RuntimeError, ValueError) as error:  # CommandError included
            problems.append(str(error))
    return lines, problems


def bundle_facts(bundle: Path):
    """The bundle's revision (VERSION) and its image archives (SHA256SUMS), or raise DrError."""
    try:
        version = dict(line.split('=', 1) for line in (bundle / 'VERSION').read_text().splitlines() if '=' in line)
        sums = (bundle / 'SHA256SUMS').read_text().splitlines()
    except OSError as error:
        raise DrError(f'no complete offline bundle at {bundle}: {error.strerror or error}') from None
    archives = [line.split(maxsplit=1)[1] for line in sums
                if len(line.split(maxsplit=1)) == 2 and line.split(maxsplit=1)[1].startswith('./images/')]
    return version.get('source_revision', ''), archives


def secret_exists(name: str) -> bool:
    """True if this host holds the Podman secret."""
    return run('podman', 'secret', 'exists', name, allowed=(0, 1), timeout=TIMEOUT,
               description=f'Podman secret {name} check').returncode == 0


def readiness(config: Config, exists: Optional[Callable[[str], bool]] = None):
    """Could this host take over now? Returns (lines, problems), like check().

    A missing piece found during a fire is found too late, so the scheduled
    check looks for it every 15 minutes, on both hosts: the offline bundle the
    failover loads its images from is the revision of the operations package
    that installed this tool, every image archive it lists is there, and this
    host holds every DR secret, the replication CA included. It reads files
    and asks Podman; it never contacts the other host.
    """
    exists = exists or secret_exists
    problems = []
    revision, archives = '', []
    if not config.bundle:
        problems.append('the DR settings name no offline bundle: run app-ops install-dr-tool again')
    else:
        bundle = Path(config.bundle)
        try:
            revision, archives = bundle_facts(bundle)
        except DrError as error:
            problems.append(str(error))
        else:
            if config.revision and revision != config.revision:
                problems.append(f'the offline bundle at {bundle} is revision {revision or "unknown"}, but the DR '
                                f'tool was installed from {config.revision}: stage the same revision on both hosts')
            missing = [name for name in archives if not (bundle / name).is_file()]
            if not archives or missing:
                problems.append(f'the offline bundle at {bundle} lacks image archives: '
                                + (', '.join(missing) or 'SHA256SUMS lists none'))
    names = transfer.transfer_names()
    absent = [name for name in names if not exists(name)]
    if absent:
        problems.append('DR secrets missing on this host: ' + ', '.join(absent))
    if problems:
        return [], problems
    return [f'Ready to take over: offline bundle {revision[:12]} with {len(archives)} image archives, '
            f'all {len(names)} DR secrets'], []


def guarded(look):
    """look() for nginx's certificate (nginx_tls), with a failure to look reported as a problem."""
    try:
        return look()
    except (OSError, RuntimeError, ValueError) as error:
        return [], [f'cannot check the nginx certificate: {error}']


def parser():
    """Command-line arguments; see the module docstring."""
    result = argparse.ArgumentParser(description='Inspect and safely promote the complete local database group.')
    result.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    commands.add_parser('check', help='Read-only check of replication, archiving, disk space and readiness')
    commands.add_parser('renew-tls', help="Renew each primary's replication certificate when it is due")
    configure = commands.add_parser('configure', help='Write the private DR configuration for the complete group')
    configure.add_argument('--primary-name', required=True)
    configure.add_argument('--primary-address', required=True)
    configure.add_argument('--standby-name', required=True)
    configure.add_argument('--rpo-target-seconds', type=int, default=settings.RPO_TARGET_SECONDS)
    configure.add_argument('--revision', default='', help='the operations package revision that installs the tool')
    configure.add_argument('--bundle', default='', help="this host's offline bundle directory")
    preflight = commands.add_parser('preflight')
    preflight.add_argument('--confirm-primary-fenced', required=True)
    promote = commands.add_parser('promote')
    promote.add_argument('--confirm-primary-fenced', required=True)
    promote.add_argument('--confirm-promotion', required=True)
    return result


def main(arguments: Optional[Sequence[str]] = None):
    """Run one command; print errors as 'ERROR: ...' and return exit code 1."""
    args = parser().parse_args(arguments)
    try:
        if args.command == 'configure':
            print(json.dumps({'changed': write_config(args.config, args.primary_name, args.primary_address,
                                                      args.standby_name, args.rpo_target_seconds,
                                                      args.revision, args.bundle)}))
            return 0
        if args.command == 'check':
            lines, problems = check()
            try:
                ready, missing = readiness(load_config(args.config))
            except RuntimeError as error:  # unreadable settings, or Podman not answering
                ready, missing = [], [str(error)]
            lines, problems = lines + ready, problems + missing
            nginx_lines, nginx_problems = guarded(nginx_tls.readiness)
            lines, problems = lines + nginx_lines, problems + nginx_problems
            print('\n'.join(lines))
            for problem in problems:
                print(f'ERROR: {problem}', file=sys.stderr)
            return 1 if problems else 0
        if args.command == 'renew-tls':
            lines, problems = renew_tls()
            print('\n'.join(lines))
            for problem in problems:
                print(f'ERROR: {problem}', file=sys.stderr)
            return 1 if problems else 0
        tool = StandbyGroup(load_config(args.config), journal_path=args.config.with_name(settings.PROMOTION_RECORD))
        if args.command == 'status':
            print('\n'.join(tool.status_lines()))
        elif args.command == 'preflight':
            states = tool.preflight(args.confirm_primary_fenced)
            print('Preflight passed for all applications: ' + ', '.join(states) +
                  '. Primary endpoints are unreachable and every local apply lag is zero.')
        else:
            states = tool.promote(args.confirm_primary_fenced, args.confirm_promotion)
            print('Promotion completed: every database is writable: ' + ', '.join(states))
        return 0
    except (OSError, RuntimeError, ValueError) as error:  # DrError, CommandError and replication's own
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
