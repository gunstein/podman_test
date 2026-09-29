"""Local DR checks and guarded promotion of the complete application group.

Installed as /opt/todo/bin/app_dr.py on the standby host and run there:

  app_dr.py configure ...   write the DR settings (done by install-dr-tool)
  app_dr.py status          show each database's role, lag and primary reachability
  app_dr.py preflight ...   read-only: may the group be promoted now?
  app_dr.py promote ...     preflight, then promote every database

Promotion is all or nothing in intent, but cannot be undone. Every step is
written to a promotion record first, and a failed or partial promotion is
never retried automatically: a person must look at the record and at each
database first.
"""
import argparse
import fcntl
import json
import os
import socket
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

# This DR tool reuses the installer (app_installer) and the DR building
# blocks (app_dr_host). app-ops installs it in /opt/todo/bin and both
# packages side by side in /opt/todo/lib, so they are found in lib next to
# bin. In a checkout, set PYTHONPATH=deploy/installer:deploy/dr instead.
# deploy/dr/README.md ("Where DR finds the installer") has the whole rule.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from app_dr_host import replication  # noqa: E402
from app_installer import apps, quadlet, settings  # noqa: E402
from app_installer.commands import run  # noqa: E402

DEFAULT_CONFIG = Path.home() / settings.DR_CONFIG / 'todo-dr.json'
DEFAULT_JOURNAL = DEFAULT_CONFIG.with_name(settings.PROMOTION_RECORD)
# Status and promotion commands answer within seconds. Two minutes is
# generous, and keeps a hung command from stalling a failover.
TIMEOUT = 120


class DrError(RuntimeError):
    """A check of this tool's own refused: wrong confirmation, host, settings or state.

    A failed command raises commands.CommandError, and a broken replication
    rule (replication.require_standby) a RuntimeError that says which; main()
    prints all of them as "ERROR: ...".
    """


@dataclass(frozen=True)
class Config:
    """The DR settings written by 'configure': who the primary is, and where."""

    primary_name: str
    primary_address: str
    standby_name: str
    rpo_target_seconds: int
    applications: tuple = ()


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
                        int(raw.get('rpo_target_seconds', raw.get('rpo_seconds'))), tuple(names))
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
                 rpo_target_seconds: int) -> bool:
    """Private config for the complete group; a literal address keeps the fencing reachability check honest."""
    try:
        primary_address = replication.address(primary_address)
    except ValueError as error:
        raise DrError(f'primary_address must be a literal IPv4 address: {primary_address!r}') from error
    raw = {'applications': [database.name for database in apps.REPLICATED_DATABASES], 'primary_name': primary_name,
           'primary_address': primary_address, 'standby_name': standby_name,
           'rpo_target_seconds': rpo_target_seconds}
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
                    replication.promote(database, query=self._query, command=lambda *argv: run(
                        *argv, timeout=TIMEOUT, description=f'{database.name}: PostgreSQL promotion'))
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


def parser():
    """Command-line arguments; see the module docstring."""
    result = argparse.ArgumentParser(description='Inspect and safely promote the complete local database group.')
    result.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    configure = commands.add_parser('configure', help='Write the private DR configuration for the complete group')
    configure.add_argument('--primary-name', required=True)
    configure.add_argument('--primary-address', required=True)
    configure.add_argument('--standby-name', required=True)
    configure.add_argument('--rpo-target-seconds', type=int, default=settings.RPO_TARGET_SECONDS)
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
                                                      args.standby_name, args.rpo_target_seconds)}))
            return 0
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
