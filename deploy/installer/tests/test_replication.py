import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_installer import apps, replication


class ReplicationTests(unittest.TestCase):
    def test_registry_replicates_each_independent_database(self):
        self.assertEqual([app.name for app in apps.APPS], ['todo', 'notes'])
        app = apps.SHARED_RESOURCE_OWNER
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
            elif kwargs.get('input') == replication.REFRESH_HBA_SCRIPT:
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
        for app in apps.APPS:
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
        names = [app.name for app in apps.APPS]
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

    def test_refresh_hba_replaces_inherited_subnet_only_for_the_selected_role(self):
        with tempfile.TemporaryDirectory() as temp:
            hba = Path(temp) / 'pg_hba.conf'
            hba.write_text('host replication todo_replicator 10.88.0.0/24 scram-sha-256\n'
                           'host replication notes_replicator 10.77.0.0/24 scram-sha-256\n'
                           'host all all 127.0.0.1/32 scram-sha-256\n')

            def command(*argv, **kwargs):
                if argv[:3] == ('podman', 'network', 'inspect'):
                    self.assertEqual(argv[3], apps.NETWORK)
                    return subprocess.CompletedProcess(argv, 0, '[{"subnets":[{"subnet":"10.99.0.0/24"}]}]', '')
                self.assertEqual(argv[:4], ('podman', 'exec', '-i', 'todo-postgres'))
                return subprocess.run(argv[4:], input=kwargs.get('input'), check=True, text=True,
                                      capture_output=True, env={**os.environ, 'PGDATA': temp})

            with patch.object(replication, 'run', side_effect=command), patch.object(replication, 'sql') as sql:
                self.assertTrue(replication.refresh_hba(apps.APPS[0]))
                self.assertFalse(replication.refresh_hba(apps.APPS[0]))
                sql.assert_called_with(apps.APPS[0], 'SELECT pg_reload_conf();')
            self.assertEqual(hba.read_text().splitlines(), [
                'host replication notes_replicator 10.77.0.0/24 scram-sha-256',
                'host all all 127.0.0.1/32 scram-sha-256',
                'host replication todo_replicator 10.99.0.0/24 scram-sha-256'])

    def test_reseed_requires_exact_host_and_fencing_before_any_command(self):
        with patch.object(replication.socket, 'gethostname', return_value='old-primary'), \
                patch.object(replication, 'run') as run:
            for fenced, confirmed in (('yes', 'old-primary'), ('old-primary is fenced', 'other-host')):
                with self.assertRaisesRegex(RuntimeError, 'Exact local hostname'):
                    replication.reseed_check(apps.APPS[0], '192.0.2.51', project_root='/tmp',
                        quadlet_dir='/tmp/q', kube_runtime_dir='/tmp/q/todo-kube-runtime',
                        rendered_manifest_dir='/tmp', confirm_fenced=fenced, confirm_reseed=confirmed)
            run.assert_not_called()

    def test_reseed_failure_cannot_delete_data_or_backup(self):
        for app in apps.APPS:
            with patch.object(replication, 'reseed_check', side_effect=RuntimeError('authentication failed')), \
                    patch.object(replication, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'authentication failed'):
                    replication.reseed_standby(app, '192.0.2.51', confirm_fenced='old is fenced', confirm_reseed='old')
                run.assert_not_called()

    def test_confirmed_reseed_orders_checks_before_cleanup_before_deletion(self):
        for app in apps.APPS:
            order = []
            with patch.object(replication, 'reseed_check',
                              side_effect=lambda *a, **k: order.append('check')) as gate, \
                    patch.object(replication, 'authenticate',
                                side_effect=lambda *a: order.append('authenticate')) as auth, \
                    patch.object(replication, 'remove_exited_containers_using',
                                side_effect=lambda *a: order.append('cleanup')) as cleanup, \
                    patch.object(replication, 'run',
                                side_effect=lambda *a, **k: order.append('run')) as run, \
                    patch.object(replication, 'bootstrap_standby', return_value=True) as bootstrap:
                self.assertTrue(replication.reseed_standby(app, '192.0.2.51',
                    confirm_fenced='old is fenced', confirm_reseed='old', project_root='/source'))
                gate.assert_called_once()
                auth.assert_called_once_with(app, '192.0.2.51')
                cleanup.assert_called_once_with(app.volume('data'))
                run.assert_called_once_with('podman', 'volume', 'rm', app.volume('data'))
                bootstrap.assert_called_once_with(app, '192.0.2.51', project_root='/source',
                                                  slot=app.replication_slot(rebuilt=True))
                self.assertEqual(order, ['check', 'authenticate', 'cleanup', 'run'])

    def test_exited_containers_on_the_data_volume_are_removed_but_a_running_one_fails_closed(self):
        volume = apps.APPS[0].volume('data')
        with patch.object(replication, 'run') as run:
            run.return_value.stdout = 'todo-postgres|exited\nold-helper|created'
            self.assertTrue(replication.remove_exited_containers_using(volume))
            run.assert_called_with('podman', 'rm', 'todo-postgres', 'old-helper')

        with patch.object(replication, 'run') as run:
            run.return_value.stdout = ''
            self.assertFalse(replication.remove_exited_containers_using(volume))
            run.assert_called_once()

        with patch.object(replication, 'run') as run:
            run.return_value.stdout = 'todo-postgres|running'
            with self.assertRaisesRegex(RuntimeError, 'todo-postgres is still running; data was not removed'):
                replication.remove_exited_containers_using(volume)
            run.assert_called_once()

    def test_reseed_check_never_contacts_the_primary(self):
        # The rebuild preflight runs reseed_check before the primary has published
        # its LAN endpoint (postgres_redundancy_primary runs later in the same
        # rebuild). It must pass without any network replication probe.
        for app in apps.APPS:
            with tempfile.TemporaryDirectory() as temp:
                quadlet_dir = Path(temp) / 'q'
                kube_runtime_dir = quadlet_dir / 'todo-kube-runtime'
                kube_runtime_dir.mkdir(parents=True)
                (kube_runtime_dir / app.manifest('config')).write_bytes(b'---\n')
                (kube_runtime_dir / app.manifest('postgres')).write_text(json.dumps({
                    'kind': 'PersistentVolumeClaim',
                    'metadata': {'name': app.volume('data')},
                }))
                quadlet_root = Path(temp) / 'source'
                (quadlet_root / 'deploy/quadlet').mkdir(parents=True)
                (quadlet_root / 'deploy/quadlet/app-network.network').write_bytes(b'')
                (quadlet_root / 'deploy/quadlet' / (app.unit('postgres') + '.j2')).write_text(
                    '{{ postgres_publish_address }}:{{ postgres_publish_port }}')

                def command(*argv, **kwargs):
                    self.assertNotIn('IDENTIFY_SYSTEM', ' '.join(str(a) for a in argv))
                    if argv[:2] == ('podman', 'info'):
                        return subprocess.CompletedProcess(argv, 0, 'true', '')
                    if argv[:2] == ('podman', 'ps'):
                        return subprocess.CompletedProcess(argv, 0, '', '')
                    if argv[:3] == ('podman', 'kube', 'play'):
                        return subprocess.CompletedProcess(argv, 0, '--no-pod-prefix', '')
                    return subprocess.CompletedProcess(argv, 0, '', '')

                with patch.object(replication, 'run', side_effect=command), \
                        patch.object(replication, 'exists', return_value=True), \
                        patch.object(replication, 'require_stopped_service'), \
                        patch.object(replication, 'authenticate') as auth:
                    replication.reseed_check(app, '192.0.2.51', project_root=str(quadlet_root),
                        quadlet_dir=str(quadlet_dir), kube_runtime_dir=str(kube_runtime_dir),
                        rendered_manifest_dir=str(kube_runtime_dir),
                        confirm_fenced=replication.socket.gethostname() + ' is fenced',
                        confirm_reseed=replication.socket.gethostname())
                    auth.assert_not_called()

    def test_stopped_service_requires_zero_pids_and_preserves_failed_state(self):
        for state, main, control, accepted in [('inactive', '0', '0', True), ('failed', '0', '0', True),
                                              ('failed', '10', '0', False), ('inactive', '0', '12', False),
                                              ('activating', '0', '0', False)]:
            result = subprocess.CompletedProcess([], 0,
                f'LoadState=loaded\nActiveState={state}\nMainPID={main}\nControlPID={control}\n', '')
            with patch.object(replication, 'run', return_value=result) as run:
                if accepted:
                    replication.require_stopped_service('notes-postgres.service')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'zero MainPID/ControlPID'):
                        replication.require_stopped_service('notes-postgres.service')
                self.assertEqual(run.call_count, 1)
                self.assertIn('show', run.call_args.args)


class PublishPrimariesTests(unittest.TestCase):
    DATABASES = apps.REPLICATED_DATABASES

    def publish(self, bootstrap, changed=(), access_changed=False, legacy=False, readonly=None):
        steps = self.steps = []

        def run(*argv, input=None, allowed=(0,)):
            steps.append(argv)
            return subprocess.CompletedProcess(argv, 0, '', '')

        def preflight(directory):
            steps.append(('legacy-preflight',))
            if legacy:
                raise RuntimeError('Unsupported per-container Quadlets are installed.')

        def require_primary(app):
            steps.append(('require-primary', app.name))
            if app.name == readonly:
                raise RuntimeError(f'{app.name}: expected a writable primary')

        def identity(name):
            return lambda app, *args: (steps.append((name, app.name) + args), access_changed)[1]

        def install(project_root, quadlet_dir, runtime, rendered, publish_address, *, app):
            steps.append(('install', app.name, publish_address))
            return app.name in changed

        with patch.object(replication, 'run', run), \
                patch.object(replication.install, 'preflight', preflight), \
                patch.object(replication, 'require_primary', require_primary), \
                patch.object(replication, 'configure_primary', identity('configure-primary')), \
                patch.object(replication, 'refresh_hba', identity('refresh-hba')), \
                patch.object(replication.workloads, 'install_postgres', install), \
                patch.object(replication.quadlet, 'systemctl', lambda *args: steps.append(('systemctl',) + args)), \
                patch.object(replication.keycloak, 'wait',
                             lambda path, *a, hostname=None, **k: steps.append(('wait', path, hostname))):
            result = replication.publish_primaries(
                '192.0.2.10', bootstrap=bootstrap, project_root='/staged', quadlet_dir='/q',
                kube_runtime_dir='/q/todo-kube-runtime', rendered_manifest_dir='/staged/generated/kube-runtime')
        return result, steps

    def first(self, steps, predicate):
        return next(i for i, step in enumerate(steps) if predicate(step))

    def test_every_primary_is_checked_before_the_first_write(self):
        for bootstrap in (True, False):
            with self.subTest(bootstrap=bootstrap):
                _result, steps = self.publish(bootstrap)
                gates = [i for i, step in enumerate(steps) if step[0] == 'require-primary']
                self.assertEqual([steps[i][1] for i in gates], [d.name for d in self.DATABASES])
                first_write = self.first(steps, lambda s: s[0] in ('configure-primary', 'refresh-hba', 'install'))
                self.assertLess(max(gates), first_write)
                self.assertEqual(steps[0], ('legacy-preflight',))

    def test_bootstrap_creates_identities_and_redundancy_only_refreshes_access(self):
        _result, steps = self.publish(True)
        self.assertEqual([s for s in steps if s[0] in ('configure-primary', 'refresh-hba')],
                         [('configure-primary', d.name, '192.0.2.10') for d in self.DATABASES])
        _result, steps = self.publish(False)
        self.assertEqual([s for s in steps if s[0] in ('configure-primary', 'refresh-hba')],
                         [('refresh-hba', d.name) for d in self.DATABASES])

    def test_every_database_is_published_on_the_node_address(self):
        _result, steps = self.publish(True)
        self.assertEqual([s for s in steps if s[0] == 'install'],
                         [('install', d.name, '192.0.2.10') for d in self.DATABASES])

    def test_unchanged_group_is_neither_stopped_nor_restarted_but_verified(self):
        result, steps = self.publish(False)
        self.assertEqual(result, {'changed': False, 'restarted': []})
        self.assertFalse([s for s in steps if s[:3] == ('systemctl', '--user', 'stop')
                          or s[:2] == ('systemctl', 'restart')])
        self.assertEqual([s[-1] for s in steps if s[:3] == ('podman', 'wait', '--condition=healthy')],
                         [d.resource('postgres') for d in self.DATABASES])
        self.assertIn(('systemctl', 'start', 'shared-proxy.service'), steps)
        self.assertEqual([s for s in steps if s[0] == 'wait'],
                         [('wait', '/ready', app.hostname) for app in apps.APPS])

    def test_changed_databases_restart_behind_one_application_tier_stop(self):
        changed = [d.name for d in self.DATABASES[1:]]
        result, steps = self.publish(True, changed=changed)
        self.assertEqual(result, {'changed': True, 'restarted': changed})
        stops = [s for s in steps if s[:3] == ('systemctl', '--user', 'stop')]
        self.assertEqual(stops, [('systemctl', '--user', 'stop', *apps.services(databases=False))])
        restarts = [s for s in steps if s[:2] == ('systemctl', 'restart')]
        self.assertEqual([s[2] for s in restarts], [d.service('postgres') for d in self.DATABASES[1:]])
        self.assertLess(steps.index(stops[0]), steps.index(restarts[0]))
        self.assertLess(steps.index(restarts[-1]), steps.index(('systemctl', 'start', 'shared-proxy.service')))
        self.assertLess(steps.index(('systemctl', 'start', 'shared-proxy.service')),
                        self.first(steps, lambda s: s[0] == 'wait'))

    def test_changed_access_alone_reports_change_without_restart(self):
        result, _steps = self.publish(False, access_changed=True)
        self.assertEqual(result, {'changed': True, 'restarted': []})

    def test_legacy_quadlets_or_a_readonly_last_database_refuse_before_any_write(self):
        for options in ({'legacy': True}, {'readonly': self.DATABASES[-1].name}):
            with self.subTest(**options):
                with self.assertRaises(RuntimeError):
                    self.publish(True, changed=[d.name for d in self.DATABASES], **options)
                self.assertFalse([step for step in self.steps
                                  if step[0] in ('configure-primary', 'refresh-hba', 'install', 'systemctl')])

    def test_node_address_must_be_a_literal_ip(self):
        with patch.object(replication.install, 'preflight') as preflight:
            with self.assertRaises(ValueError):
                replication.publish_primaries('primary.example', bootstrap=True, project_root='/s',
                                              quadlet_dir='/q', kube_runtime_dir='/q/todo-kube-runtime',
                                              rendered_manifest_dir='/r')
            preflight.assert_not_called()
