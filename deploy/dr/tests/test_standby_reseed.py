"""Copying a standby again without a failover (D10): every guard refuses before anything is deleted.

The standby must prove it is a read-only standby, run nothing but its
databases and reach the primary; only then does it stop its databases and
delete their data volumes. On the primary, only an idle slot is dropped.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / 'installer')]
from app_dr_host import replication  # noqa: E402
from app_installer import platform_file  # noqa: E402

GROUP = platform_file.checkout().replicated_databases
HOST = replication.socket.gethostname()
STANDBY = dict(in_recovery=True, transaction_read_only=True)


def done(stdout=''):
    return subprocess.CompletedProcess([], 0, stdout, '')


class StandbyHost:
    """A standby that may be re-seeded; tests break one condition each."""

    def __init__(self):
        self.states = {database.name: dict(STANDBY) for database in GROUP}
        self.running = []  # application services that are active
        self.unreachable = set()
        self.commands, self.authenticated = [], []

    def run(self, *argv, **kwargs):
        self.commands.append(tuple(str(arg) for arg in argv))
        if argv[:3] == ('systemctl', '--user', 'is-active'):
            return done('\n'.join('active' if service in self.running else 'inactive' for service in argv[3:]))
        return done()

    def path(self, database, primary_address):
        if database.name in self.unreachable:
            raise RuntimeError(f'{database.name}: replication port is not reachable; data was not removed')

    def call(self, function, *arguments):
        with patch.object(replication, 'run', side_effect=self.run), \
                patch.object(replication, 'status', side_effect=lambda database: self.states[database.name]), \
                patch.object(replication, 'replication_path', side_effect=self.path), \
                patch.object(replication, 'authenticate',
                             side_effect=lambda database, address: self.authenticated.append(database.name)), \
                patch.object(replication, 'require_stopped_service'):
            return function(*arguments)

    def changing(self):
        return [argv for argv in self.commands if argv[:3] != ('systemctl', '--user', 'is-active')]


class StandbyReseedCheckTests(unittest.TestCase):
    def test_a_healthy_standby_passes_and_changes_nothing(self):
        host = StandbyHost()
        self.assertIs(host.call(replication.standby_reseed_check, platform_file.checkout(), '192.0.2.11'), False)
        self.assertEqual(host.authenticated, [database.name for database in GROUP])
        self.assertEqual(host.changing(), [])

    def test_each_broken_condition_refuses_before_the_primary_is_contacted_or_anything_changes(self):
        def writable(host):
            host.states['notes'] = dict(in_recovery=False, transaction_read_only=False)

        def app_running(host):
            host.running.append('todo-app.service')

        def unreachable(host):
            host.unreachable.add('keycloak')

        for name, breaks, message in (('a primary', writable, 'not a read-only standby'),
                                      ('an app service', app_running, 'todo-app.service'),
                                      ('no path', unreachable, 'not reachable')):
            with self.subTest(name):
                host = StandbyHost()
                breaks(host)
                with self.assertRaisesRegex(RuntimeError, message):
                    host.call(replication.standby_reseed_check, platform_file.checkout(), '192.0.2.11')
                self.assertEqual(host.changing(), [])
                self.assertNotIn('keycloak', host.authenticated)

    def test_the_primary_address_must_be_a_literal_ipv4_address(self):
        with self.assertRaises(ValueError):
            StandbyHost().call(replication.standby_reseed_check, platform_file.checkout(), 'primary.example')


class EraseStandbyGroupTests(unittest.TestCase):
    def test_after_every_check_it_stops_the_databases_and_removes_only_their_data_volumes(self):
        host = StandbyHost()
        erased = host.call(replication.erase_standby_group, platform_file.checkout(), '192.0.2.11', HOST)
        self.assertEqual(erased, [database.name for database in GROUP])
        self.assertEqual(host.changing()[0], ('systemctl', '--user', 'stop', *(d.service for d in GROUP)))
        removed = [argv[3:] for argv in host.changing() if argv[:3] == ('podman', 'volume', 'rm')]
        self.assertEqual(removed, [(database.volume('data'),) for database in GROUP])
        self.assertFalse([argv for argv in host.commands if 'backup' in ' '.join(argv) or '--force' in argv])

    def test_a_wrong_hostname_or_a_failed_check_erases_nothing(self):
        host = StandbyHost()
        with self.assertRaisesRegex(RuntimeError, 'exact local hostname'):
            host.call(replication.erase_standby_group, platform_file.checkout(), '192.0.2.11', 'todo-standby-typo')
        self.assertEqual(host.commands, [])
        host = StandbyHost()
        host.states['todo'] = dict(in_recovery=False, transaction_read_only=False)
        with self.assertRaisesRegex(RuntimeError, 'never erases a primary'):
            host.call(replication.erase_standby_group, platform_file.checkout(), '192.0.2.11', HOST)
        self.assertEqual(host.changing(), [])


class SlotTests(unittest.TestCase):
    """On the primary: which slot the standby uses, and dropping it only when idle."""

    def primary(self, function, *arguments, answers):
        statements = []

        def sql(database, statement, **kwargs):
            statements.append(statement)
            return answers.pop(0)

        with patch.object(replication, 'require_primary'), patch.object(replication, 'sql', side_effect=sql):
            return function(GROUP[0], *arguments), statements

    def test_the_slot_is_the_one_there_or_the_bootstrap_name(self):
        self.assertEqual(self.primary(replication.standby_slot, answers=['todo_rebuilt_standby'])[0],
                         'todo_rebuilt_standby')
        self.assertEqual(self.primary(replication.standby_slot, answers=[''])[0], GROUP[0].replication_slot())
        with self.assertRaisesRegex(RuntimeError, 'more than one replication slot'):
            self.primary(replication.standby_slot, answers=['todo_rebuilt_standby\ntodo_standby'])

    def test_only_an_idle_slot_is_dropped(self):
        dropped, statements = self.primary(replication.drop_idle_slot, 'todo_standby', answers=['f', ''])
        self.assertTrue(dropped)
        self.assertIn("pg_drop_replication_slot('todo_standby')", statements[1])
        self.assertEqual(self.primary(replication.drop_idle_slot, 'todo_standby', answers=[''])[0], False)
        with self.assertRaisesRegex(RuntimeError, 'still in use'):
            self.primary(replication.drop_idle_slot, 'todo_standby', answers=['t'])
        with self.assertRaises(ValueError):
            self.primary(replication.drop_idle_slot, "x'); DROP TABLE todos; --", answers=[])

    def test_a_standby_is_refused_before_any_sql(self):
        with patch.object(replication, 'require_primary', side_effect=RuntimeError('expected a writable primary')), \
                patch.object(replication, 'sql') as sql:
            for function, arguments in ((replication.standby_slot, ()), (replication.drop_idle_slot, ('todo_standby',))):
                with self.assertRaisesRegex(RuntimeError, 'writable primary'):
                    function(GROUP[0], *arguments)
        sql.assert_not_called()


if __name__ == '__main__':
    unittest.main()
