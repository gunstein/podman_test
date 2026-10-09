"""backup.py: nightly base backups of a single host, pruning, the timer and restoring the latest backup."""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, backup, cli  # noqa: E402
from fake_host import FakeHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc)
WEEK = ['base-20261001T023000Z', 'base-20261002T020000Z', 'base-20261008T023000Z', 'base-20261009T023000Z']
DISK = (100 * 2**30, 50 * 2**30, 50 * 2**30)


class BackupHost(FakeHost):
    """A host whose databases answer as primaries (or standbys) and whose backup volumes hold WEEK."""

    def __init__(self, recovery='f', names=WEEK, latest=WEEK[-1], **kwargs):
        super().__init__(**kwargs)
        self.recovery, self.names, self.latest = recovery, names, latest

    def answer(self, argv, input):
        if 'psql' in argv and input == 'SELECT pg_is_in_recovery();\n':
            container = argv[3]
            return 0, (self.recovery[container] if isinstance(self.recovery, dict) else self.recovery) + '\n'
        if argv[:2] == ['podman', 'exec'] and 'cat "$1"; ls "$2"' in argv:
            return 0, '\n'.join([self.latest, *self.names, 'lost+found']) + '\n'
        if argv[:2] == ['podman', 'run'] and any('cat /backup/LATEST' in part for part in argv):
            return 0, self.latest + '\n'
        return super().answer(argv, input)


class BackupTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.quadlet = Path(temporary.name)
        self.runtime = self.quadlet / 'todo-kube-runtime'
        self.runtime.mkdir()

    def install_units(self, databases=apps.REPLICATED_DATABASES, applications=apps.APPS):
        for name in [database.unit for database in databases] + [app.unit for app in applications]:
            (self.runtime / name).write_text('[Kube]\n')


class CreateAndPruneTests(BackupTest):
    def test_a_backup_runs_inside_the_database_container_and_is_latest_only_once_verified(self):
        database = apps.APPS[0].database
        with BackupHost() as host:
            self.assertEqual(backup.create(database, lambda: NOW), 'base-20261009T023000Z')
        commands = [argv for argv in host.calls if argv[:2] == ['podman', 'exec']]
        self.assertTrue(all(argv[2] == database.container for argv in commands))
        steps = [next(word for word in argv[3:] if word in ('sh', 'pg_basebackup', 'pg_verifybackup'))
                 for argv in commands]
        self.assertEqual(steps, ['sh', 'pg_basebackup', 'pg_verifybackup', 'sh'])
        basebackup = commands[1]
        self.assertIn('--username=todo', basebackup)
        self.assertIn(f'--pgdata={backup.BASE}/base-20261009T023000Z', basebackup)
        self.assertIn('--wal-method=stream', basebackup)  # self-contained: no WAL archive needed
        self.assertEqual(commands[-1][-2:], ['base-20261009T023000Z', backup.LATEST])

    def test_backups_older_than_the_kept_days_go_but_never_the_latest(self):
        self.assertEqual(backup.expired(WEEK, WEEK[-1], backup.cutoff(7, lambda: NOW)), WEEK[:2])
        self.assertEqual(backup.expired(WEEK[:1], WEEK[0], backup.cutoff(1, lambda: NOW)), [])
        database = apps.APPS[0].database
        with BackupHost() as host:
            self.assertEqual(backup.prune(database, 7, lambda: NOW), WEEK[:2])
        deletion = host.calls[-1]
        self.assertEqual(deletion[-3:], [backup.BASE, *WEEK[:2]])
        with BackupHost(latest='garbage') as host, self.assertRaisesRegex(RuntimeError, 'nothing was deleted'):
            backup.prune(database, 7, lambda: NOW)
        self.assertFalse([argv for argv in host.calls if 'rm -rf' in ' '.join(argv)])

    def test_the_deletion_script_removes_only_the_named_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in WEEK:
                (base / name / 'global').mkdir(parents=True)
            script = 'base=$1; shift; for name do rm -rf -- "$base/$name"; done'
            subprocess.run(['sh', '-ec', script, 'delete', str(base), *WEEK[:2]], check=True)
            self.assertEqual(sorted(path.name for path in base.iterdir()), WEEK[2:])


class NightlyTests(BackupTest):
    def nightly(self, host, disk=DISK):
        with host:
            return backup.nightly(7, self.quadlet, lambda: NOW, disk)

    def test_every_installed_database_is_backed_up_then_pruned(self):
        self.install_units()
        host = BackupHost()
        lines, problems = self.nightly(host)
        self.assertEqual(problems, [])
        self.assertEqual(lines[0], 'todo: verified base backup base-20261009T023000Z; deleted 2 older than 7 days: '
                                   'base-20261001T023000Z, base-20261002T020000Z')
        self.assertEqual(lines[-1], 'Disk: 50% free (51200 MiB)')
        order = [('backup' if 'pg_basebackup' in argv else 'prune' if 'rm -rf' in ' '.join(argv) else None)
                 for argv in host.calls]
        self.assertEqual([step for step in order if step], ['backup', 'prune'] * len(apps.REPLICATED_DATABASES))

    def test_only_the_installed_databases_are_backed_up(self):
        self.install_units(databases=[apps.APPS[0].database, apps.KEYCLOAK_DATABASE],
                           applications=[apps.APPS[0]])
        lines, _ = self.nightly(BackupHost())
        self.assertEqual([line.split(':')[0] for line in lines[:-1]], ['todo', 'keycloak'])

    def test_a_standby_backs_up_nothing_and_a_split_group_nothing_either(self):
        self.install_units()
        host = BackupHost(recovery='t')
        self.assertEqual(self.nightly(host), (['standby: nothing to back up; the primary takes the backups'], []))
        self.assertFalse([argv for argv in host.calls if 'pg_basebackup' in argv])
        host = BackupHost(recovery={'todo-postgres': 'f', 'notes-postgres': 't', 'keycloak-postgres': 'f'})
        with self.assertRaisesRegex(RuntimeError, 'nothing was backed up'):
            self.nightly(host)
        self.assertFalse([argv for argv in host.calls if 'pg_basebackup' in argv])

    def test_a_nearly_full_disk_is_a_problem_after_the_backup(self):
        self.install_units()
        lines, problems = self.nightly(BackupHost(), disk=(100 * 2**30, 95 * 2**30, 5 * 2**30))
        self.assertEqual(len(lines), len(apps.REPLICATED_DATABASES))
        self.assertEqual(problems, ['only 5% of the disk is free (5120 MiB); the backup wants 10%'])

    def test_the_command_exits_1_on_a_problem_and_needs_a_day(self):
        with patch.object(backup, 'nightly', return_value=(['todo: verified base backup x'], ['disk'])), \
                patch('sys.stdout'), patch('sys.stderr'):
            self.assertEqual(cli.main(['backup', 'nightly', '--keep-days', '7']), 1)
        with patch('sys.stderr'):
            self.assertEqual(cli.main(['backup', 'nightly', '--keep-days', '0']), 1)


class RestoreTests(BackupTest):
    def restore(self, host, confirm='this-host'):
        with host, patch.object(backup.socket, 'gethostname', return_value='this-host'):
            return backup.restore(confirm, self.quadlet)

    def test_checks_then_stops_everything_then_restores_then_starts_in_order(self):
        self.install_units()
        host = BackupHost()
        self.assertEqual(self.restore(host), {database.name: WEEK[-1] for database in apps.REPLICATED_DATABASES})
        kinds = []
        for argv in host.calls:
            if argv[:2] == ['podman', 'run'] and any('cat /backup/LATEST' in part for part in argv):
                kinds.append('check')
            elif argv[:3] == ['systemctl', '--user', 'stop']:
                kinds.append('stop')
            elif argv[:2] == ['podman', 'run'] and backup.RESTORE_SCRIPT in argv:
                kinds.append('restore')
            elif argv[:3] == ['systemctl', '--user', 'start']:
                kinds.append('start')
        databases = len(apps.REPLICATED_DATABASES)
        self.assertEqual(kinds[:databases + 1 + databases], ['check'] * databases + ['stop'] + ['restore'] * databases)
        stop = next(argv for argv in host.calls if argv[:3] == ['systemctl', '--user', 'stop'])
        self.assertEqual(stop[3:], apps.services())
        starts = [argv[3] for argv in host.calls if argv[:3] == ['systemctl', '--user', 'start']]
        self.assertEqual(starts[:databases], [database.service for database in apps.REPLICATED_DATABASES])
        self.assertEqual(starts[-1], 'shared-proxy.service')
        restore = next(argv for argv in host.calls if backup.RESTORE_SCRIPT in argv)
        self.assertIn('todo-postgres-data:/data:z', restore)
        self.assertIn('todo-postgres-backup:/backup:ro,z', restore)

    def test_a_wrong_confirmation_or_a_missing_backup_changes_nothing(self):
        self.install_units()
        for host, confirm, message in ((BackupHost(), 'other-host', 'exactly this host'),
                                       (BackupHost(latest=''), 'this-host', 'no verified backup')):
            with self.subTest(message=message), self.assertRaisesRegex((ValueError, RuntimeError), message):
                self.restore(host, confirm)
            self.assertFalse([argv for argv in host.calls if argv[:3] == ['systemctl', '--user', 'stop']
                              or backup.RESTORE_SCRIPT in argv])

    def test_the_restore_script_replaces_the_data_with_the_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, base = root / 'data', root / 'backup/base' / WEEK[-1]
            (data / 'base').mkdir(parents=True)
            (data / 'base/new-since-backup').write_text('lost')
            (data / 'standby.signal').write_text('')
            base.mkdir(parents=True)
            (base / 'PG_VERSION').write_text('17\n')
            (base / 'standby.signal').write_text('')
            script = backup.RESTORE_SCRIPT.replace('/data', str(data)).replace('/backup', str(root / 'backup'))
            subprocess.run(['sh', '-ec', script, 'restore', WEEK[-1]], check=True)
            self.assertEqual(sorted(path.name for path in data.iterdir()), ['PG_VERSION'])

    def test_the_command_refuses_a_dr_host_before_anything(self):
        with patch.object(cli.install, 'require_single_host', side_effect=RuntimeError('single-host deployment')), \
                patch.object(backup, 'restore') as restore, patch('sys.stderr'):
            self.assertEqual(cli.main(['backup', 'restore', '--confirm-restore', 'x']), 1)
        restore.assert_not_called()


class TimerTests(unittest.TestCase):
    def test_the_installer_writes_the_units_once_and_keeps_the_dr_schedule(self):
        with FakeHost() as host:
            self.assertTrue(backup.install_timer('/bundle/deploy/installer'))
            service = (host.units / 'todo-backup.service').read_text()
            self.assertIn('Environment=PYTHONPATH=/bundle/deploy/installer\n', service)
            self.assertIn('ExecStart=/usr/bin/python3 -m app_installer backup nightly --keep-days 7\n', service)
            self.assertEqual((host.units / 'todo-backup.timer').read_text(),
                             (ROOT / 'deploy/dr/systemd/todo-backup.timer').read_text())
            self.assertIn(['systemctl', '--user', 'enable', '--now', 'todo-backup.timer'], host.calls)
            host.calls.clear()
            self.assertFalse(backup.install_timer('/bundle/deploy/installer'))
            self.assertFalse(host.ran('systemctl', '--user', 'enable'))
            self.assertTrue(backup.remove_timer())
            self.assertEqual(list(host.units.iterdir()), [])
            self.assertFalse(backup.remove_timer())

    def test_the_timer_runs_a_module_the_offline_bundle_carries(self):
        self.assertTrue((ROOT / 'deploy/installer/app_installer/backup.py').is_file())
        self.assertNotIn('deploy/dr', backup.service_unit('/x'))
        self.assertEqual(os.path.basename(backup.__file__), 'backup.py')


if __name__ == '__main__':
    unittest.main()
