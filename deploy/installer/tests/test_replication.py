import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_installer import apps, replication


class ReplicationTests(unittest.TestCase):
    def test_registry_replicates_each_independent_database(self):
        self.assertEqual([app.name for app in apps.REPLICATED_APPS], ['todo', 'notes'])
        app = apps.IDENTITY_DATABASE_APP
        self.assertEqual(app.replication_slot(), 'todo_standby')
        self.assertEqual(app.replication_slot(rebuilt=True), 'todo_rebuilt_standby')
        self.assertEqual(app.replication_passfile(), '.todo-replication.pgpass')

    def test_primary_is_idempotent_and_checks_password_without_exposing_it_in_argv(self):
        state = dict(secret=False, role=False, hba=False)
        calls = []

        def execute(*argv, **kwargs):
            calls.append((argv, kwargs))
            statement = kwargs.get('input', '')
            output = ''
            if argv[:3] == ('podman', 'secret', 'create'):
                state['secret'] = True
            elif 'pg_is_in_recovery()' in statement:
                output = 'f|off|||0'
            elif argv[:3] == ('podman', 'network', 'inspect'):
                output = json.dumps([{'subnets': [{'subnet': '10.89.0.0/24'}]}])
            elif 'replication-hba' in argv:
                output = '' if state['hba'] else 'changed'
                state['hba'] = True
            elif 'SELECT rolcanlogin' in statement:
                output = 't|t|f|f|f|f' if state['role'] else ''
            elif 'ALTER ROLE' in statement:
                state['role'] = True
            elif '--command=IDENTIFY_SYSTEM;' in argv:
                output = '123|1|0/10|'
            return subprocess.CompletedProcess(argv, 0, output, '')

        with patch.object(replication, 'run', side_effect=execute), \
                patch.object(replication, 'exists', side_effect=lambda *_: state['secret']), \
                patch.object(replication.secrets, 'read', return_value='A' * 32):
            self.assertTrue(replication.configure_primary(apps.APPS[0], '192.0.2.50'))
            self.assertFalse(replication.configure_primary(apps.APPS[0], '192.0.2.50'))
        self.assertTrue(any('--command=IDENTIFY_SYSTEM;' in argv for argv, _ in calls))
        self.assertTrue(all('A' * 32 not in ' '.join(map(str, argv)) for argv, _ in calls))
        self.assertEqual(sum('ALTER ROLE' in kw.get('input', '') for _, kw in calls), 1)

    def test_primary_refuses_readonly_before_creating_a_secret(self):
        with patch.object(replication, 'status', return_value={'in_recovery': True}), \
                patch.object(replication, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'writable primary'):
                replication.configure_primary(apps.APPS[0], '192.0.2.50')
            run.assert_not_called()

    def test_bootstrap_preserves_the_canonical_pvc_and_final_selinux_handoff(self):
        import yaml
        app = apps.APPS[0]
        claim = {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {
            'name': app.volume('data'), 'annotations': {
                'volume.podman.io/uid': '999', 'volume.podman.io/gid': '999'}}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / app.manifest('postgres')).write_text(yaml.safe_dump_all([
                claim, {'kind': 'Pod', 'metadata': {'name': app.resource('postgres')}}]))
            with patch.object(replication, 'run', return_value=subprocess.CompletedProcess([], 0, '1|1|0/10|', '')) as run, \
                    patch.object(replication, 'exists', side_effect=lambda kind, _: kind != 'volume'), \
                    patch.object(replication.workloads, 'install_postgres'), \
                    patch.object(replication.quadlet, 'systemctl'), \
                    patch.object(replication, 'status', return_value={
                        'in_recovery': True, 'transaction_read_only': True}):
                self.assertTrue(replication.bootstrap_standby(
                    app, '192.0.2.50', project_root=root, quadlet_dir=root,
                    kube_runtime_dir=root / 'todo-kube-runtime', rendered_manifest_dir=root))
            commands = [c.args for c in run.call_args_list]
            creation = next(c for c in run.call_args_list if c.args[:3] == ('podman', 'kube', 'play'))
            self.assertEqual(yaml.safe_load(creation.kwargs['input']), claim)
            probe = next(i for i, c in enumerate(commands) if '--command=IDENTIFY_SYSTEM;' in c)
            backup = next(i for i, c in enumerate(commands) if 'pg_basebackup' in c)
            self.assertLess(probe, commands.index(creation.args))
            self.assertLess(commands.index(creation.args), backup)
            self.assertIn('--create-slot', commands[backup])
            self.assertIn('--slot=todo_standby', commands[backup])
            helpers = [c for c in commands if '--volume' in c]
            self.assertIn('todo-postgres-data:/var/lib/postgresql/data:U,Z', helpers[0])
            self.assertIn('todo-postgres-data:/var/lib/postgresql/data:z', helpers[-1])
            self.assertIn('.todo-replication.pgpass', helpers[-1])
            self.assertFalse(any(c[:3] == ('podman', 'volume', 'rm') for c in commands))

    def test_bootstrap_refuses_existing_data_before_any_command(self):
        with patch.object(replication, 'data_claim', return_value='claim'), \
                patch.object(replication, 'exists', return_value=True), \
                patch.object(replication, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'never overwrites'):
                replication.bootstrap_standby(apps.APPS[0], '192.0.2.50', project_root='/tmp',
                                             quadlet_dir='/tmp', kube_runtime_dir='/tmp/todo-kube-runtime',
                                             rendered_manifest_dir='/tmp')
            run.assert_not_called()

    def test_authentication_failure_cannot_create_a_volume(self):
        with patch.object(replication, 'data_claim', return_value='claim'), \
                patch.object(replication, 'exists', side_effect=lambda kind, _: kind != 'volume'), \
                patch.object(replication, 'run', return_value=subprocess.CompletedProcess([], 2, '', '')) as run:
            with self.assertRaisesRegex(RuntimeError, 'authentication failed'):
                replication.bootstrap_standby(apps.APPS[0], '192.0.2.50', project_root='/tmp',
                                             quadlet_dir='/tmp', kube_runtime_dir='/tmp/todo-kube-runtime',
                                             rendered_manifest_dir='/tmp')
            self.assertEqual(run.call_count, 1)
            self.assertIn('--command=IDENTIFY_SYSTEM;', run.call_args.args)

    def test_promote_refuses_unreplayed_wal(self):
        with patch.object(replication, 'status', return_value={
            'in_recovery': True, 'transaction_read_only': True,
            'receive_lsn': '0/20', 'replay_lsn': '0/10', 'apply_lag_bytes': 16,
        }), patch.object(replication, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'not fully replayed'):
                replication.promote(apps.APPS[0])
            run.assert_not_called()

    def test_streaming_requires_each_apps_own_usable_slot(self):
        for app in apps.REPLICATED_APPS:
            slot = app.replication_slot()
            with patch.object(replication, 'require_primary'), patch.object(replication, 'sql') as sql:
                sql.side_effect = [f'{slot}|192.0.2.51|streaming|async|0', f'{slot}|t|reserved|1000|']
                self.assertEqual(replication.streaming_status(app)['slot'][0], slot)
                self.assertTrue(all(call.args[0] == app for call in sql.call_args_list))
                for invalid in (f'{slot}|f|reserved|1000|', f'{slot}|t|lost||wal_removed'):
                    sql.side_effect = [f'{slot}|192.0.2.51|streaming|async|0', invalid]
                    with self.assertRaisesRegex(RuntimeError, 'losing WAL or invalidated'):
                        replication.streaming_status(app)

    def test_incomplete_promotion_record_cannot_expose_any_application(self):
        names = [app.name for app in apps.REPLICATED_APPS]
        with tempfile.TemporaryDirectory() as temp:
            journal = Path(temp) / 'promotion.json'
            for decision in ({'state': 'failed', 'applications': names, 'completed': names},
                             {'state': 'promoting', 'applications': names, 'completed': names[:1]},
                             {'state': 'complete', 'applications': names[:1], 'completed': names[:1]}):
                journal.write_text(json.dumps(decision))
                with patch.object(replication, 'run') as run:
                    with self.assertRaisesRegex(RuntimeError, 'complete database group'):
                        replication.require_promoted_group(journal)
                    run.assert_not_called()
