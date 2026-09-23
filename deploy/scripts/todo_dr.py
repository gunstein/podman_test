"""Local DR checks and guarded promotion of the complete application group."""
import argparse
import fcntl
import json
import os
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

# Checkout/operations package and trusted target installation, respectively.
for location in ('installer', 'lib'):
    directory = Path(__file__).resolve().parents[1] / location
    if (directory / 'todo_installer').is_dir():
        sys.path.insert(0, str(directory))
        break
from todo_installer import apps, replication  # noqa: E402

DEFAULT_CONFIG = Path.home() / '.config/todo/todo-dr.json'
DEFAULT_JOURNAL = DEFAULT_CONFIG.with_name('promotion.json')


class DrError(RuntimeError):
    """An expected, operator-actionable DR error."""


@dataclass(frozen=True)
class Config:
    primary_name: str
    primary_address: str
    standby_name: str
    rpo_target_seconds: int
    applications: tuple = ()


@dataclass(frozen=True)
class DatabaseStatus:
    in_recovery: bool
    transaction_read_only: bool
    receive_lsn: str
    replay_lsn: str
    apply_lag_bytes: int


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess]
Connector = Callable[[str, int, float], bool]


def run_command(arguments: Sequence[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(list(arguments), check=False, capture_output=True, text=True, timeout=timeout)


def tcp_reachable(address: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        names = raw.get('applications', [])
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError('applications must be a list of registered names')
        config = Config(str(raw['primary_name']), str(raw['primary_address']), str(raw['standby_name']),
                        int(raw.get('rpo_target_seconds', raw.get('rpo_seconds'))), tuple(names))
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise DrError(f'Cannot read valid DR configuration from {path}: {error}') from error
    if not all((config.primary_name, config.primary_address, config.standby_name)):
        raise DrError(f'DR configuration contains an empty host identity: {path}')
    if config.rpo_target_seconds <= 0:
        raise DrError('rpo_target_seconds must be greater than zero')
    return config


class TodoDr:
    def __init__(self, config: Config, runner: Runner = run_command,
                 connector: Connector = tcp_reachable, journal_path: Path = DEFAULT_JOURNAL):
        self.config, self.runner, self.connector = config, runner, connector
        self.applications = tuple(apps.REPLICATED_APPS)
        expected = tuple(app.name for app in self.applications)
        if (config.applications and config.applications != expected) or (
                not config.applications and len(expected) != 1):
            raise DrError('DR configuration must explicitly match the complete ordered application registry')
        self.journal_path = Path(journal_path)

    def _run(self, arguments, description):
        try:
            result = self.runner(arguments, 120)
        except subprocess.TimeoutExpired as error:
            raise DrError(f'{description} timed out after 120 seconds') from error
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise DrError(f'{description} failed' + (f': {detail}' if detail else ''))
        return result.stdout.strip()

    def service_status(self, app=None):
        app = app or self.applications[0]
        return self._run(['systemctl', '--user', 'is-active', app.service('postgres')],
                         f'{app.name}: PostgreSQL systemd status check')

    def container_health(self, app=None):
        app = app or self.applications[0]
        return self._run(['podman', 'inspect', '--format', '{{.State.Health.Status}}',
                          app.resource('postgres')], f'{app.name}: PostgreSQL container health check')

    def _query(self, app, statement):
        return self._run(['podman', 'exec', app.resource('postgres'), 'psql', '--username', app.name,
                         '--dbname', 'postgres', '--tuples-only', '--no-align', '--field-separator=|',
                         '--command', statement], f'{app.name}: PostgreSQL recovery query')

    def database_status(self, app=None):
        app = app or self.applications[0]
        try:
            return DatabaseStatus(**replication.status(app, query=self._query))
        except (ValueError, RuntimeError) as error:
            raise DrError(str(error)) from error

    def primary_reachable(self, app=None):
        app = app or self.applications[0]
        return self.connector(self.config.primary_address, app.replication_port, 2.0)

    def status_lines(self) -> List[str]:
        lines = []
        for app in self.applications:
            service, health = self.service_status(app), self.container_health(app)
            database = self.database_status(app)
            primary = 'reachable' if self.primary_reachable(app) else 'unreachable'
            lines += [f'Application: {app.name}', f'Service: {service}', f'Container: {health}',
                      f"Database role: {'standby' if database.in_recovery else 'promoted primary'}",
                      f"Writable: {'no' if database.transaction_read_only else 'yes'}",
                      f'Receive LSN: {database.receive_lsn or "not available"}',
                      f'Replay LSN: {database.replay_lsn or "not available"}',
                      f'Local apply lag: {database.apply_lag_bytes} bytes',
                      f'Primary endpoint {self.config.primary_address}:{app.replication_port}: {primary}']
        lines.append('Configured RPO target (informational): at most '
                     f'{self.config.rpo_target_seconds} seconds')
        if self.journal_path.exists():
            lines.append(f'Promotion decision record: {self.journal_path}')
        return lines

    def preflight(self, fencing_confirmation):
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
        for app in self.applications:
            if self.service_status(app) != 'active':
                raise DrError(f'{app.name}: PostgreSQL systemd service is not active')
            if self.container_health(app) != 'healthy':
                raise DrError(f'{app.name}: PostgreSQL container is not healthy')
            database = self.database_status(app)
            if not database.in_recovery or not database.transaction_read_only:
                raise DrError(f'{app.name}: local PostgreSQL is not a read-only standby')
            if not database.receive_lsn or not database.replay_lsn:
                raise DrError(f'{app.name}: standby receive or replay LSN is unavailable')
            if database.apply_lag_bytes != 0:
                raise DrError(f'{app.name}: standby has unreplayed local WAL: {database.apply_lag_bytes} bytes')
            if self.primary_reachable(app):
                raise DrError('Primary PostgreSQL still answers at '
                              f'{self.config.primary_address}:{app.replication_port}; fencing is not demonstrated')
            states[app.name] = database
        return states

    @contextmanager
    def _promotion_lock(self):
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
                for app in self.applications:
                    replication.promote(app, query=self._query,
                                        command=lambda *argv: self._run(argv, f'{app.name}: PostgreSQL promotion'))
                    decision['completed'].append(app.name)
                    self._record(decision)
                result = {app.name: self.database_status(app) for app in self.applications}
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
    result = argparse.ArgumentParser(description='Inspect and safely promote the complete local database group.')
    result.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    preflight = commands.add_parser('preflight')
    preflight.add_argument('--confirm-primary-fenced', required=True)
    promote = commands.add_parser('promote')
    promote.add_argument('--confirm-primary-fenced', required=True)
    promote.add_argument('--confirm-promotion', required=True)
    return result


def main(arguments: Optional[Sequence[str]] = None):
    args = parser().parse_args(arguments)
    try:
        tool = TodoDr(load_config(args.config), journal_path=args.config.with_name('promotion.json'))
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
    except (DrError, OSError, RuntimeError, ValueError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
