"""The read-only replication checks: exact commands, exact slots and every boundary.

These close gaps that mutation testing found (docs/MUTATION-TESTING.md): a
check that looked at the wrong slot, accepted one byte of lag, or ran a
slightly different command would otherwise still pass every test.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_installer import apps, replication

APP = apps.APPS[1]
HEALTHY_STANDBY = 't|on|0/3000060|0/3000060'


def done(stdout=''):
    return subprocess.CompletedProcess([], 0, stdout, '')


class StatusTests(unittest.TestCase):
    def status(self, output):
        return replication.status(APP, query=lambda app, statement: output)

    def test_every_field_is_parsed(self):
        self.assertEqual(self.status('t|on|0/3000060|0/3000050'), {
            'in_recovery': True, 'transaction_read_only': True, 'receive_lsn': '0/3000060',
            'replay_lsn': '0/3000050', 'apply_lag_bytes': 16})
        self.assertEqual(self.status('f|off||'), {
            'in_recovery': False, 'transaction_read_only': False, 'receive_lsn': '',
            'replay_lsn': '', 'apply_lag_bytes': 0})

    def test_malformed_output_is_refused(self):
        for output in ('t|on|0/1', 't|on|0/1|0/1|0', 'x|on|0/1|0/1', 't|yes|0/1|0/1', '',
                       't|on|3000060|0/1', 't|on|0/1|0/xyz', 't|on|0/123456789|0/1'):
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, 'invalid database status'):
                self.status(output)

    def test_the_query_goes_to_the_selected_database(self):
        seen = []
        replication.status(APP, query=lambda app, statement: seen.append(app) or HEALTHY_STANDBY)
        self.assertEqual(seen, [APP])

    def test_require_primary_and_standby_use_the_given_query(self):
        replication.require_primary(APP, query=lambda app, statement: 'f|off||')
        replication.require_standby(APP, query=lambda app, statement: HEALTHY_STANDBY)


class UnreplayedBytesTests(unittest.TestCase):
    """Bytes received but not replayed yet; never negative."""

    def test_lsn_is_a_64_bit_position(self):
        self.assertEqual(replication.lsn('0/3000060'), 0x3000060)
        self.assertEqual(replication.lsn('1/0'), 1 << 32)
        self.assertEqual(replication.lsn('A/ff'), (10 << 32) + 255)
        self.assertEqual(replication.lsn('1f/0'), 31 << 32)
        for invalid in ('3000060', '0/3000060/1', '0/', '/1', '0/123456789', 'g/0'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'Invalid WAL position'):
                replication.lsn(invalid)

    def test_the_three_cases(self):
        self.assertEqual(replication.unreplayed_bytes('0/3000060', '0/3000000'), 96)
        self.assertEqual(replication.unreplayed_bytes('0/3000060', '0/3000060'), 0)
        # After a walreceiver restart: receive restarts at the segment start (seen in acceptance).
        self.assertEqual(replication.unreplayed_bytes('0/3000000', '0/3000060'), 0)
        self.assertEqual(replication.unreplayed_bytes('0/3000061', '0/3000060'), 1)
        self.assertEqual(replication.unreplayed_bytes('1/0', '0/FFFFFFFF'), 1)

    def test_an_unknown_position_counts_as_nothing(self):
        for receive, replay in (('', '0/1'), ('0/1', ''), ('', '')):
            self.assertEqual(replication.unreplayed_bytes(receive, replay), 0)


class RequireStandbyTests(unittest.TestCase):
    """Promotion trusts this check: a writable, lagging or disconnected standby must never pass."""

    def check(self, output):
        return replication.require_standby(APP, query=lambda app, statement: output)

    def test_a_caught_up_standby_passes(self):
        self.assertEqual(self.check(HEALTHY_STANDBY)['apply_lag_bytes'], 0)
        # Receive behind replay after a walreceiver restart: nothing left to replay.
        restarted = self.check('t|on|0/3000000|0/3000060')
        self.assertEqual((restarted['receive_lsn'], restarted['apply_lag_bytes']), ('0/3000000', 0))
        asked = []
        replication.require_standby(APP, query=lambda app, statement: asked.append(app) or HEALTHY_STANDBY)
        self.assertEqual(asked, [APP])

    def test_each_unsafe_state_is_refused(self):
        cases = [('f|on|0/1|0/1', 'expected a read-only standby'),
                 ('t|off|0/1|0/1', 'expected a read-only standby'),
                 ('f|off|0/1|0/1', 'expected a read-only standby'),
                 ('t|on||0/1', 'not fully replayed'),
                 ('t|on|0/1|', 'not fully replayed'),
                 ('t|on||', 'not fully replayed'),
                 ('t|on|0/2|0/1', 'not fully replayed'),
                 ('t|on|0/3000060|0/3000000', 'not fully replayed')]
        for output, message in cases:
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, message):
                self.check(output)


class StreamingStatusTests(unittest.TestCase):
    CONNECTION = 'notes_standby|192.0.2.11|streaming|async|0'
    SLOT = 'notes_standby|t|reserved|1024|'

    def run_check(self, connection=CONNECTION, slot=SLOT, **options):
        statements = []

        def sql(app, statement, **kwargs):
            statements.append(statement)
            return connection if 'pg_stat_replication' in statement else slot

        with patch.object(replication, 'require_primary') as primary, \
                patch.object(replication, 'sql', side_effect=sql):
            result = replication.streaming_status(APP, **options)
        primary.assert_called_once_with(APP)
        return result, statements

    def test_each_mode_checks_its_own_slot(self):
        for options, slot in (({}, APP.replication_slot()), ({'rebuilt': False}, APP.replication_slot()),
                              ({'rebuilt': True}, APP.replication_slot(rebuilt=True))):
            with self.subTest(**options):
                _, statements = self.run_check(**options)
                self.assertEqual(len(statements), 2)
                for statement in statements:
                    self.assertIn(f"= '{slot}';", statement)

    def test_a_healthy_connection_and_slot_are_returned(self):
        result, _ = self.run_check(slot='notes_standby|t|extended|1024|')
        self.assertEqual(result['connection'][2:4], ['streaming', 'async'])
        self.assertEqual(result['slot'][2], 'extended')

    def test_each_unhealthy_connection_is_refused(self):
        for connection in ('', 'notes_standby|192.0.2.11|streaming|async',
                           'notes_standby|192.0.2.11|catchup|async|0', 'notes_standby|192.0.2.11|streaming|sync|0'):
            with self.subTest(connection=connection), self.assertRaisesRegex(RuntimeError, 'not streaming'):
                self.run_check(connection=connection)

    def test_each_unhealthy_slot_is_refused(self):
        for slot in ('', 'notes_standby|t|reserved|1024', 'notes_standby|f|reserved|1024|',
                     'notes_standby|t|unreserved|0|', 'notes_standby|t|lost|0|',
                     'notes_standby|t|reserved|1024|wal_removed'):
            with self.subTest(slot=slot), self.assertRaisesRegex(RuntimeError, 'inactive, losing WAL'):
                self.run_check(slot=slot)


class ArchiveHealthTests(unittest.TestCase):
    def check(self, output):
        with patch.object(replication, 'sql', return_value=output):
            return replication.archive_health(APP)

    def test_the_report_names_every_field(self):
        self.assertEqual(self.check('f|off|on|000000010000000000000003|2|healthy'), {
            'in_recovery': 'f', 'read_only': 'off', 'archive_mode': 'on',
            'last_archived_wal': '000000010000000000000003', 'historical_failures': '2',
            'archive_health': 'healthy'})

    def test_each_unhealthy_archive_is_refused(self):
        for output in ('t|on|on|0003|0|healthy', 'f|on|on|0003|0|healthy', 'f|off|off|0003|0|healthy',
                       'f|off|on||0|healthy', 'f|off|on|0003|1|failed', 'f|off|on|0003|0'):
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, 'WAL archiving'):
                self.check(output)


class CommandContractTests(unittest.TestCase):
    """The checks read exactly these commands; a changed argument would read something else."""

    def record(self, answers):
        commands = []

        def run(*argv, **kwargs):
            commands.append(tuple(argv))
            for prefix, output in answers:
                if tuple(argv[:len(prefix)]) == prefix:
                    return done(output)
            return done()
        return commands, run

    def test_require_stopped_service(self):
        commands, run = self.record([((), 'LoadState=loaded\nActiveState=inactive\nMainPID=0\nControlPID=0\n')])
        with patch.object(replication, 'run', side_effect=run):
            replication.require_stopped_service('notes-postgres.service')
        self.assertEqual(commands, [('systemctl', '--user', 'show', 'notes-postgres.service',
                                     '--property=LoadState', '--property=ActiveState',
                                     '--property=MainPID', '--property=ControlPID')])

    def test_require_quarantined_group_checks_every_service_then_every_container(self):
        checked = []
        commands, run = self.record([])
        with patch.object(replication, 'require_stopped_service', side_effect=checked.append), \
                patch.object(replication, 'run', side_effect=run):
            self.assertIs(replication.require_quarantined_group(), False)
        self.assertEqual(checked, apps.services())
        self.assertEqual(commands, [('podman', 'ps', '--format', '{{.Names}}')])

    def test_rebuild_primary_check(self):
        commands, run = self.record([(('systemctl',), 'active')])
        statements, looked_up = [], []

        def sql(app, statement, **kwargs):
            statements.append((app, statement))
            return 't' if 'rolreplication' in statement else '0'

        def exists(kind, name):
            looked_up.append((kind, name))
            return True

        with patch.object(replication, 'require_primary') as primary, \
                patch.object(replication, 'run', side_effect=run), \
                patch.object(replication, 'exists', side_effect=exists), \
                patch.object(replication, 'sql', side_effect=sql):
            self.assertIs(replication.rebuild_primary_check(APP), False)
        primary.assert_called_once_with(APP)
        self.assertEqual(commands, [('systemctl', '--user', 'is-active', APP.service('postgres'))])
        self.assertEqual(looked_up, [('secret', APP.secret('replicator')), ('volume', APP.volume('backup'))])
        self.assertEqual([app for app, _ in statements], [APP, APP])
        self.assertIn(f"rolname = '{APP.database_role('replicator')}'", statements[0][1])
        self.assertIn(f"slot_name = '{APP.replication_slot(rebuilt=True)}'", statements[1][1])

    def test_require_promoted_group_checks_each_database_exactly(self):
        names = [database.name for database in apps.REPLICATED_DATABASES]
        commands, run = self.record([(('systemctl',), 'active'), (('podman',), 'healthy')])
        with tempfile.TemporaryDirectory() as temporary:
            journal = Path(temporary) / 'promotion.json'
            journal.write_text(json.dumps({'state': 'complete', 'applications': names, 'completed': names}))
            with patch.object(replication, 'run', side_effect=run), \
                    patch.object(replication, 'require_primary') as primary:
                replication.require_promoted_group(journal)
        expected = []
        for database in apps.REPLICATED_DATABASES:
            expected += [('systemctl', '--user', 'is-active', database.service('postgres')),
                         ('podman', 'inspect', '--format', '{{.State.Health.Status}}',
                          database.resource('postgres'))]
        self.assertEqual(commands, expected)
        self.assertEqual([call.args for call in primary.call_args_list],
                         [(database,) for database in apps.REPLICATED_DATABASES])

    def test_one_wrong_field_in_the_promotion_record_is_enough_to_refuse(self):
        names = [database.name for database in apps.REPLICATED_DATABASES]
        complete = {'state': 'complete', 'applications': names, 'completed': names}
        with tempfile.TemporaryDirectory() as temporary:
            journal = Path(temporary) / 'promotion.json'
            for key, value in (('state', 'promoting'), ('applications', names[:-1]), ('completed', names[:-1])):
                with self.subTest(key=key), patch.object(replication, 'run') as run:
                    journal.write_text(json.dumps({**complete, key: value}))
                    with self.assertRaisesRegex(RuntimeError, 'complete database group'):
                        replication.require_promoted_group(journal)
                    run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
