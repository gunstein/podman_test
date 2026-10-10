import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offline_bundle  # noqa: E402
from app_installer import (  # noqa: E402
    apps,
    cli,
    install,
    keycloak,
    kube_play,
    secrets,
    settings,
    uninstall,
)
from fake_host import FakeHost, RenderingHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def copy_project(root):
    """The parts of the repository a build-mode install reads, copied into root."""
    for part in ('deploy/environments', 'deploy/quadlet'):
        shutil.copytree(ROOT / part, root / part)
    return root


class InstallTests(unittest.TestCase):
    def exercise_install(self, mode, repeat=False, source_override=None, applications=None):
        """Install in server mode from a real offline bundle, or in dev mode by building; return the calls."""
        applications = (apps.APPS[0],) if applications is None else applications
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'quadlet'
            runtime = directory / 'platform-kube-runtime'
            if mode == 'server':
                offline_bundle.build(root, applications)
                project, how, Host = ROOT, dict(deployment_mode='offline', bundle_directory=root), FakeHost
            else:
                project, how, Host = copy_project(root), dict(deployment_mode='build'), RenderingHost

            with Host(unit_directory=runtime, source=source_override) as host, \
                    patch.object(keycloak, 'configure') as configure, \
                    patch.object(settings, 'DEV_STATE_FILE', root / 'app-installer-dev.json'):
                calls = host.calls
                install.install(project, mode=mode, quadlet_dir=directory, applications=applications, **how)
                configure.assert_called_once_with('fixture-password', [
                    (app.keycloak_client, app.hostname) for app in applications])
                if mode == 'server':
                    self.assertEqual(len(list(runtime.glob('*.kube'))), 2 * len(applications) + 3)
                    self.assertEqual(len(host.ran('systemctl', '--user', 'show')),
                                     2 * len(applications) + 3)
                else:
                    self.assertFalse(directory.exists())
                if repeat:
                    calls.clear()
                    install.install(project, mode=mode, quadlet_dir=directory, applications=applications, **how)
                    self.assertFalse(host.ran('systemctl', '--user', 'stop'))
            bootstrap = [i for i, a in enumerate(calls) if a[-1] == 'backend.setup_roles']
            self.assertEqual(len(bootstrap), 2 * len(applications))
            wait = next(i for i, a in enumerate(calls) if a[:2] == ['podman', 'wait'])
            self.assertLess(wait, bootstrap[0])
            for index in bootstrap:
                self.assertIn('--cap-drop', calls[index])
                self.assertIn('no-new-privileges', calls[index])
            return calls, bootstrap

    def test_six_pod_server_and_repeat(self):
        calls, bootstrap = self.exercise_install('server', applications=apps.APPS, repeat=True)
        for app in apps.APPS:
            setup = [calls[i] for i in bootstrap if f'DATABASE_HOST={app.database.container}' in calls[i]]
            self.assertEqual(len(setup), 2)
            for command in setup:
                self.assertIn(app.database.secret('db'), command)
                self.assertIn(app.database.secret('migrator'), command)
                self.assertIn(app.database.secret('app'), command)
        for service in ('keycloak', 'keycloak-postgres', 'shared-proxy'):
            self.assertEqual(calls.count(['systemctl', '--user', 'start', service + '.service']), 1)

    def test_six_pod_dev_order(self):
        calls, bootstrap = self.exercise_install('dev', applications=apps.APPS)
        plays = [(i, a) for i, a in enumerate(calls)
                 if a[:3] == ['podman', 'kube', 'play'] and '--help' not in a]
        self.assertEqual([Path(a[-1]).stem for _, a in plays], [
            'todo-postgres', 'notes-postgres', 'keycloak-postgres', 'keycloak', 'todo-app', 'notes-app', 'shared-proxy'])
        self.assertLess(bootstrap[1], plays[2][0])
        self.assertLess(plays[-1][0], bootstrap[2])

    def test_server_install(self):
        calls, bootstrap = self.exercise_install('server')
        app = calls.index(['systemctl', '--user', 'start', 'todo-app.service'])
        self.assertLess(bootstrap[0], app)
        self.assertLess(app, bootstrap[1])
        self.assertLess(bootstrap[1], calls.index(['systemctl', '--user', 'start', 'shared-proxy.service']))

    def test_server_repeat_does_not_restart_unchanged_workloads(self):
        self.exercise_install('server', repeat=True)

    def test_server_repeat_only_restarts_the_apps_whose_manifests_changed(self):
        applications = apps.APPS
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'quadlet'
            offline_bundle.build(root, applications)

            with FakeHost(unit_directory=directory / 'platform-kube-runtime') as host, \
                    patch.object(keycloak, 'configure'), \
                    patch.object(settings, 'DEV_STATE_FILE', root / 'app-installer-dev.json'):
                install.install(ROOT, mode='server', deployment_mode='offline',
                                bundle_directory=root, quadlet_dir=directory, applications=applications)
                changed = root / 'generated/target/manifests' / apps.APPS[0].manifest
                changed.write_text(changed.read_text() + '# changed\n')
                host.calls.clear()
                install.install(ROOT, mode='server', deployment_mode='offline',
                                bundle_directory=root, quadlet_dir=directory, applications=applications)
            stopped = {a[3] for a in host.ran('systemctl', '--user', 'stop')}
            self.assertEqual(stopped, {'todo-app.service'})

    def test_source_path_must_be_the_expected_workload_unit(self):
        for source in ('/tmp/todo-app.container', '/tmp/todo-app.kube',
                       '/tmp/platform-kube-runtime/unrelated.kube'):
            with self.subTest(source=source), self.assertRaisesRegex(RuntimeError, 'SourcePath'):
                self.exercise_install('server', source_override=source)

    def test_dev_install_order_and_publication(self):
        calls, bootstrap = self.exercise_install('dev')
        plays = [(i, a) for i, a in enumerate(calls)
                 if a[:3] == ['podman', 'kube', 'play'] and '--help' not in a]
        self.assertEqual([Path(a[-1]).stem for _, a in plays],
                         ['todo-postgres', 'keycloak-postgres', 'keycloak', 'todo-app', 'shared-proxy'])
        self.assertLess(plays[0][0], bootstrap[0])
        self.assertLess(bootstrap[0], plays[1][0])
        self.assertLess(plays[-1][0], bootstrap[1])
        self.assertIn('127.0.0.1:8080:8080', plays[-1][1])
        self.assertIn('127.0.0.1:8443:8443', plays[-1][1])
        self.assertFalse(any(a[0] == 'systemctl' for a in calls))

    def test_dev_repeat_preserves_pods_and_manifest_changes_reapply(self):
        from app_installer.apps import APPS
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in ('todo-app', 'todo-postgres', 'todo-config', 'keycloak-postgres', 'keycloak-config',
                        'keycloak', 'shared-proxy'):
                (directory / (name + '.yaml')).write_text('fixture: ' + name)
            state = directory / '.state.json'
            present = set()

            def exists(kind, name):
                return kind == 'network' or name in present

            def run(*args, **kwargs):
                if args[:3] == ('podman', 'kube', 'play') and '--down' not in args:
                    pod = {'app': 'todo-app', 'postgres': 'todo-postgres'}.get(
                        Path(args[-1]).stem, Path(args[-1]).stem)
                    present.add(pod)
                return subprocess.CompletedProcess(args, 0, 'Running', '')

            with patch.object(kube_play, 'exists', side_effect=exists), \
                    patch.object(kube_play, 'run', side_effect=run) as command, \
                    patch.object(kube_play, 'setup_roles') as roles:
                self.assertTrue(kube_play.up(directory, (APPS[0],), state))
                self.assertEqual(roles.call_count, 2)
                command.reset_mock()
                roles.reset_mock()
                self.assertFalse(kube_play.up(directory, (APPS[0],), state))
                roles.assert_not_called()
                self.assertTrue(all(call.args[:3] == ('podman', 'pod', 'inspect')
                                    for call in command.call_args_list))
                (directory / 'todo-config.yaml').write_text('changed: true')
                self.assertTrue(kube_play.up(directory, (APPS[0],), state))
                downs = [call.kwargs['input'] for call in command.call_args_list
                         if '--down' in call.args]
                self.assertEqual(downs, ['fixture: ' + name for name in (
                    'shared-proxy', 'todo-app', 'keycloak', 'keycloak-postgres', 'todo-postgres')])
                self.assertEqual(roles.call_count, 2)

    def test_down_uses_reverse_order_and_only_existing_files(self):
        with tempfile.TemporaryDirectory() as temp, patch('app_installer.kube_play.run') as run, \
                patch.object(secrets, 'remove_kube_volumes'):
            root = Path(temp)
            for name in ('shared-proxy', 'todo-app', 'todo-postgres'):
                (root / (name + '.yaml')).touch()
            state = root / '.state.json'
            self.assertTrue(kube_play.down(root, state_file=state))
            self.assertEqual([c.args for c in run.call_args_list], [
                ('podman', 'kube', 'play', '--down', root / (name + '.yaml'))
                for name in ('shared-proxy', 'todo-app', 'todo-postgres')])
            self.assertFalse(state.exists())

    def test_down_uses_the_recorded_yaml_after_its_file_is_gone(self):
        # The next render replaces the whole directory, so a file up played
        # may be gone (an app left out of the selection); its pods must still go.
        with tempfile.TemporaryDirectory() as temp, patch('app_installer.kube_play.run') as run, \
                patch.object(secrets, 'remove_kube_volumes'):
            root = Path(temp)
            state = root / '.state.json'
            state.write_text(json.dumps({'fingerprint': 'x', 'teardown': ['kind: Pod # notes-app']}))
            self.assertTrue(kube_play.down(root / 'rendered-again', state_file=state))
            run.assert_called_once_with('podman', 'kube', 'play', '--down', '-',
                                        input='kind: Pod # notes-app')
            self.assertFalse(state.exists())

    def test_down_reports_when_nothing_was_installed(self):
        with tempfile.TemporaryDirectory() as temp, patch('app_installer.kube_play.run') as run, \
                patch.object(secrets, 'remove_kube_volumes'):
            root = Path(temp)
            self.assertFalse(kube_play.down(root, state_file=root / '.state.json'))
            run.assert_not_called()

    def test_down_finds_the_default_state_file_up_actually_wrote(self):
        # up() and down() must agree on the state file's location without a
        # caller passing state_file explicitly, the way the CLI's plain
        # `install --mode dev` and `down` commands actually call them.
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(settings, 'DEV_STATE_FILE', Path(temp) / 'app-installer-dev.json'), \
                patch('app_installer.kube_play.exists', return_value=False), \
                patch('app_installer.kube_play.setup_roles'), \
                patch('app_installer.kube_play.run',
                      return_value=subprocess.CompletedProcess([], 0, 'Running', '')):
            directory = Path(temp) / 'rendered'
            directory.mkdir()
            for name in ('todo-postgres', 'todo-config', 'todo-app', 'keycloak-postgres', 'keycloak-config',
                        'keycloak', 'shared-proxy'):
                (directory / (name + '.yaml')).write_text('fixture: ' + name)
            self.assertTrue(kube_play.up(directory, (apps.APPS[0],)))
            self.assertTrue(settings.DEV_STATE_FILE.is_file())

            with patch('app_installer.kube_play.run') as run, patch.object(secrets, 'remove_kube_volumes'):
                # A different directory argument: down must still find the
                # pods through the recorded state file, not through this one.
                self.assertTrue(kube_play.down(Path(temp) / 'unrelated'))
                run.assert_called()
            self.assertFalse(settings.DEV_STATE_FILE.is_file())

    def test_every_secret_is_generated_without_a_terminal(self):
        # No secret should ever require an interactive prompt: provision() must
        # work unattended (CI, scripted installs) for every one of them, not
        # just the migrator/app role passwords.
        with patch('app_installer.secrets.exists', return_value=False), \
                patch('sys.stdin.isatty', return_value=False), \
                patch('app_installer.secrets.run') as run:
            secrets.provision()
        names = {call.args[3] for call in run.call_args_list}
        self.assertEqual(names, {
            'todo-db-password', 'todo-migrator-password', 'todo-app-password',
            'notes-db-password', 'notes-migrator-password', 'notes-app-password',
            'keycloak-db-password', 'keycloak-admin-password',
        })
        for call in run.call_args_list:
            self.assertRegex(call.kwargs['input'], r'^[a-zA-Z0-9]{32}$')

    def test_generated_secrets_are_alphanumeric_and_existing_secrets_preserved(self):
        missing = {'todo-db-password', 'todo-migrator-password', 'todo-app-password',
                  'keycloak-db-password', 'keycloak-admin-password'}
        with patch('app_installer.secrets.exists', side_effect=lambda _, name: name not in missing), \
                patch('app_installer.secrets.run') as run:
            secrets.provision()
            self.assertEqual(run.call_count, len(missing))
            for call in run.call_args_list:
                self.assertRegex(call.kwargs['input'], r'^[a-zA-Z0-9]{32}$')

    def test_keycloak_uses_issuer_and_preserves_client_fields(self):
        for unchanged in (False, True):
            client = {'id': 'client', 'other-setting': True, 'redirectUris': [], 'webOrigins': []}
            if unchanged:
                client.update(redirectUris=['https://todo.test:8443/'],
                              webOrigins=['https://todo.test:8443'])
            with patch.object(keycloak, 'wait', side_effect=[{
                    'issuer': 'https://auth.test:8443/auth/realms/todo'}, {}, {}]), \
                    patch.object(keycloak, 'request', side_effect=[
                        {'access_token': 'token'}, dict(keycloak.REALM_SECURITY), [{'id': 'client'}],
                        client, None]) as request:
                self.assertEqual(keycloak.configure('password', [('todo-frontend', 'todo.test')]), not unchanged)
                if not unchanged:
                    body = request.call_args.args[2]
                    self.assertTrue(body['other-setting'])
                    self.assertEqual(body['webOrigins'], ['https://todo.test:8443'])
                    self.assertEqual(body['redirectUris'], ['https://todo.test:8443/'])

    def test_keycloak_configures_each_client_origin_and_creates_missing_clients(self):
        todo = {'id': 'todo-id', 'publicClient': True, 'attributes': {'pkce.code.challenge.method': 'S256'},
                'redirectUris': ['https://todo.test:8443/'], 'webOrigins': ['https://todo.test:8443'],
                'protocolMappers': [{'id': 'mapper-id', 'config': {
                    'included.client.audience': 'todo-frontend'}}]}
        for missing in (False, True):
            responses = [{'access_token': 'token'}, dict(keycloak.REALM_SECURITY), [{'id': 'todo-id'}], todo]
            # A missing client is copied from the realm import's client, looked up only then.
            responses += [[], [{'id': 'todo-id'}], todo, None] if missing else [[{'id': 'notes-id'}], {
                'id': 'notes-id', 'custom': True, 'redirectUris': [], 'webOrigins': []}, None]
            with patch.object(keycloak, 'wait', side_effect=[{
                    'issuer': 'https://auth.test:8443/auth/realms/todo'}, {}, {}, {}, {}]), \
                    patch.object(keycloak, 'request', side_effect=responses) as request:
                self.assertTrue(keycloak.configure('password', [
                    ('todo-frontend', 'todo.test'), ('notes-frontend', 'notes.test')]))
                call = request.call_args.args
                self.assertEqual(call[1], 'POST' if missing else 'PUT')
                self.assertEqual(call[2]['webOrigins'], ['https://notes.test:8443'])
                self.assertEqual(call[2]['redirectUris'], ['https://notes.test:8443/'])
                if missing:
                    self.assertTrue(call[2]['publicClient'])
                    self.assertEqual(call[2]['attributes']['pkce.code.challenge.method'], 'S256')
                    mapper = call[2]['protocolMappers'][0]
                    self.assertNotIn('id', mapper)
                    self.assertEqual(mapper['config']['included.client.audience'], 'notes-frontend')
                else:
                    self.assertTrue(call[2]['custom'])

    def test_realm_login_protection_is_applied_once_and_kept(self):
        with patch.object(keycloak, 'request', side_effect=[{'realm': 'todo', 'bruteForceProtected': False},
                                                            None]) as request:
            self.assertTrue(keycloak.secure_realm('token'))
            path, method, body, token = request.call_args.args
            self.assertEqual((path, method, token), ('/auth/admin/realms/todo', 'PUT', 'token'))
            self.assertEqual(body, keycloak.REALM_SECURITY)
        with patch.object(keycloak, 'request', return_value={'realm': 'todo', **keycloak.REALM_SECURITY}) as request:
            self.assertFalse(keycloak.secure_realm('token'))
            request.assert_called_once_with('/auth/admin/realms/todo', token='token')

    def test_realm_import_carries_the_same_login_protection(self):
        realm = json.loads((Path(__file__).resolve().parents[3] / 'keycloak/todo-realm.json').read_text())
        self.assertEqual({key: realm.get(key) for key in keycloak.REALM_SECURITY}, keycloak.REALM_SECURITY)

    def test_readiness_retries_connection_reset_during_proxy_startup(self):
        with patch.object(keycloak, 'request', side_effect=[ConnectionResetError(), {'status': 'ok'}]), \
                patch('app_installer.keycloak.time.sleep') as sleep:
            self.assertEqual(keycloak.wait('/health', 30, 1, 'ok'), {'status': 'ok'})
            sleep.assert_called_once_with(1)

    def test_keycloak_rejects_non_https_issuer_before_token_request(self):
        with patch.object(keycloak, 'wait', return_value={'issuer': 'http://todo/auth/realms/todo'}), \
                patch.object(keycloak, 'request') as request:
            with self.assertRaisesRegex(RuntimeError, 'HTTPS issuer'):
                keycloak.configure('password')
            request.assert_not_called()


class UninstallTests(unittest.TestCase):
    def setUp(self):
        # Uninstall removes the nightly backup timer's units; never the real ones.
        units = tempfile.TemporaryDirectory()
        self.addCleanup(units.cleanup)
        self.units = Path(units.name)
        patcher = patch.object(settings, 'SYSTEMD_USER_DIR', self.units)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_refuses_dr_secret_before_any_mutation(self):
        with patch('app_installer.install.exists', return_value=True), \
                patch('app_installer.uninstall.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'single-host deployment'):
                uninstall.uninstall()
            run.assert_not_called()

    def test_data_and_secret_removal_requires_explicit_flag(self):
        for remove_data in (False, True):
            with tempfile.TemporaryDirectory() as temp, \
                    patch('app_installer.install.exists', return_value=False), \
                    patch('app_installer.uninstall.exists',
                          side_effect=lambda kind, name: not name.endswith('-replicator-password')), \
                    patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                    patch.object(settings, 'DEV_STATE_FILE', Path(temp) / '_unused' / 'dev.json'):
                directory = Path(temp)
                (directory / 'platform-kube-runtime').mkdir()
                for name in uninstall.QUADLET_FILES:
                    (directory / name).touch()
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertTrue(uninstall.uninstall(remove_data, directory))
                self.assertFalse(list(directory.iterdir()))
                calls = [c.args[0] for c in run.call_args_list]
                self.assertEqual(['podman', 'volume', 'rm', 'todo-postgres-data'] in calls, remove_data)
                self.assertEqual(['podman', 'secret', 'rm', 'todo-db-password'] in calls, remove_data)
                self.assertNotIn(['podman', 'volume', 'rm', 'todo-postgres-backup'], calls)
                self.assertIn('app-network-network.service', calls[0])
                # platform-nginx-data holds the demo CA; it is persistent like the
                # database volumes and only goes away with --remove-data.
                self.assertEqual(['podman', 'volume', 'rm', 'platform-nginx-data'] in calls, remove_data)
                self.assertEqual(['podman', 'volume', 'rm', 'todo-caddy-data'] in calls, remove_data)
                # The Kube secrets' volumes are copies of secrets: they always go.
                for name in (apps.APPS[1].kube_secret('backend'), apps.KEYCLOAK_DATABASE.kube_secret,
                             apps.PROXY_KUBE_TLS_SECRET):
                    self.assertIn(['podman', 'volume', 'rm', name], calls)

    def test_uninstall_turns_the_nightly_backup_off_and_keeps_the_backups(self):
        for name in ('platform-backup.service', 'platform-backup.timer'):
            (self.units / name).write_text('[Unit]\n')
        with tempfile.TemporaryDirectory() as temp, \
                patch('app_installer.install.exists', return_value=False), \
                patch('app_installer.uninstall.exists', return_value=False), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                patch.object(settings, 'DEV_STATE_FILE', Path(temp) / 'dev.json'):
            self.assertTrue(uninstall.uninstall(quadlet_dir=Path(temp)))
        self.assertEqual(list(self.units.iterdir()), [])
        calls = [c.args[0] for c in run.call_args_list]
        self.assertIn(['systemctl', '--user', 'disable', '--now', 'platform-backup.timer'], calls)
        self.assertFalse([c for c in calls if c[:3] == ['podman', 'volume', 'rm'] and c[-1].endswith('-backup')])

    def test_noop_uninstall_on_an_untouched_host_reports_no_change(self):
        def command(argv, **kwargs):
            rc = 5 if argv[:3] == ['systemctl', '--user', 'stop'] else 0
            return subprocess.CompletedProcess(argv, rc, '', '')

        with tempfile.TemporaryDirectory() as temp, \
                patch('app_installer.install.exists', return_value=False), \
                patch('app_installer.uninstall.exists', return_value=False), \
                patch('app_installer.secrets.exists', return_value=False), \
                patch('subprocess.run', side_effect=command), \
                patch.object(settings, 'DEV_STATE_FILE', Path(temp) / '_unused' / 'dev.json'):
            directory = Path(temp)
            self.assertFalse(uninstall.uninstall(quadlet_dir=directory))

    def test_uninstall_removes_only_registered_pods_before_the_shared_network(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch('app_installer.install.exists', return_value=False), \
                patch('app_installer.uninstall.exists',
                      side_effect=lambda kind, name: kind == 'network'), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                patch.object(settings, 'DEV_STATE_FILE', Path(temp) / 'app-installer-dev.json'):
            directory = Path(temp) / 'systemd'
            directory.mkdir()
            state = Path(temp) / 'app-installer-dev.json'
            state.write_text('{}')
            unrelated = Path(temp) / 'unrelated.json'
            unrelated.write_text('{}')
            uninstall.uninstall(quadlet_dir=directory)
            commands = [call.args[0] for call in run.call_args_list]
            removals = [command for command in commands if command[:3] == ['podman', 'pod', 'rm']]
            self.assertEqual([command[-1] for command in removals],
                             ['shared-proxy', 'notes-app', 'todo-app', 'keycloak', 'keycloak-postgres',
                              'notes-postgres', 'todo-postgres'])
            network = commands.index(['podman', 'network', 'rm', 'app-network'])
            self.assertTrue(all(commands.index(command) < network for command in removals))
            self.assertTrue(all('--volumes' not in command and '-v' not in command for command in removals))
            self.assertFalse(state.exists())
            self.assertTrue(unrelated.exists())

    def uninstall_with(self, directory, **options):
        """uninstall on a host where every Podman object exists; the commands it ran and what it said."""
        said = io.StringIO()
        with patch('app_installer.install.exists', return_value=False), \
                patch('app_installer.uninstall.exists', return_value=True), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                patch.object(settings, 'DEV_STATE_FILE', directory / 'dev.json'), \
                contextlib.redirect_stderr(said):
            uninstall.uninstall(quadlet_dir=directory, **options)
        return [call.args[0] for call in run.call_args_list], said.getvalue()

    def test_uninstall_removes_the_old_per_container_install_and_says_so(self):
        # The lists of the retired playbook ansible/uninstall.yml (tag quadlet-reference-v1).
        old_files = ('todo.network', 'todo-postgres-data.volume', 'platform-nginx-data.volume', 'todo-caddy-data.volume',
                     'todo-postgres.container', 'todo-db-setup.container', 'todo-migrate.container',
                     'todo-db-grants.container', 'todo-backend.container', 'todo-keycloak.container',
                     'todo-frontend.container')
        old_services = ('todo-frontend', 'todo-backend', 'todo-keycloak', 'todo-db-grants', 'todo-migrate',
                        'todo-db-setup', 'todo-postgres', 'todo-network', 'todo-postgres-data-volume')
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in old_files:
                (directory / name).touch()
            commands, said = self.uninstall_with(directory)
            self.assertEqual(sorted(path.name for path in directory.iterdir()), [])
        stop = commands[0]
        self.assertEqual(stop[:3], ['systemctl', '--user', 'stop'])
        for name in old_services:
            self.assertIn(name + '.service', stop)
        for name in ('todo-frontend', 'todo-backend', 'todo-keycloak', 'todo-migrate', 'todo-db-grants',
                     'todo-db-setup', 'todo-postgres'):
            self.assertIn(['podman', 'rm', '--force', '--ignore', name], commands)
        self.assertIn(['podman', 'network', 'rm', 'todo-network'], commands)
        for image in ('localhost/todo-backend:m12', 'localhost/todo-frontend:m12', 'localhost/todo-keycloak:m12'):
            self.assertIn(['podman', 'image', 'rm', image], commands)
        self.assertIn('Removed the old per-container install (quadlet-reference-v1): ', said)
        self.assertIn('todo-keycloak.container', said)
        # A host without it hears nothing about it.
        with tempfile.TemporaryDirectory() as temp:
            self.assertNotIn('old per-container', self.uninstall_with(Path(temp))[1])

    def test_only_remove_backups_removes_the_backups_and_only_with_the_data(self):
        for options, removed in (({}, False), ({'remove_data': True}, False),
                                 ({'remove_data': True, 'remove_backups': True}, True)):
            with self.subTest(options), tempfile.TemporaryDirectory() as temp:
                commands = self.uninstall_with(Path(temp), **options)[0]
                for name in ('todo-postgres-backup', 'notes-postgres-backup', 'keycloak-postgres-backup'):
                    self.assertEqual(['podman', 'volume', 'rm', name] in commands, removed)
        with patch('app_installer.uninstall.run') as run, patch('app_installer.install.run') as checks:
            with self.assertRaisesRegex(ValueError, 'needs remove_data'):
                uninstall.uninstall(remove_backups=True)
            run.assert_not_called()
            checks.assert_not_called()

    def test_remove_backups_still_refuses_a_dr_host(self):
        with patch('app_installer.install.exists', return_value=True), \
                patch('app_installer.uninstall.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'single-host deployment'):
                uninstall.uninstall(remove_data=True, remove_backups=True)
            run.assert_not_called()

    def test_the_cli_wants_remove_data_with_remove_backups(self):
        with patch('app_installer.uninstall.uninstall') as remove, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(['uninstall', '--remove-backups'])
            remove.assert_not_called()

    def test_unexpected_stop_failure_preserves_files(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch('app_installer.install.exists', return_value=False), \
                patch('app_installer.uninstall.exists', return_value=False), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            directory = Path(temp)
            target = directory / 'app-network.network'
            target.touch()
            with self.assertRaises(RuntimeError):
                uninstall.uninstall(quadlet_dir=directory)
            self.assertTrue(target.exists())


class FailureBoundaryTests(unittest.TestCase):
    def test_each_dr_marker_refuses_uninstall(self):
        for marker in ('.config/platform/todo-standby-entrypoint.sh',
                       '/opt/platform/bin/app_dr.py', '/opt/platform/bin/app_backup.py',
                       '/opt/platform/bin/todo_dr.py', '/opt/platform/bin/todo_backup.py'):
            with patch('app_installer.install.exists', return_value=False), \
                    patch.object(Path, 'exists', autospec=True,
                                 side_effect=lambda p, marker=marker: str(p).endswith(marker)), \
                    patch('app_installer.uninstall.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'single-host deployment'):
                    uninstall.uninstall()
                run.assert_not_called()

    def test_install_refuses_a_replicated_host_before_any_command(self):
        # Rerunning install on a replicated primary would drop the LAN publication
        # of its databases and cut off the standby (seen in an acceptance run).
        markers = ('.config/platform/todo-standby-entrypoint.sh', '/opt/platform/bin/app_dr.py',
                   '/opt/platform/bin/app_backup.py', '/opt/platform/bin/todo_dr.py', '/opt/platform/bin/todo_backup.py')
        cases = [(marker, lambda kind, name: False) for marker in markers]
        cases += [(None, lambda kind, name, d=d: name == d.secret('replicator'))
                  for d in apps.REPLICATED_DATABASES]
        for marker, secret in cases:
            with self.subTest(marker=marker), patch('app_installer.install.exists', side_effect=secret), \
                    patch.object(Path, 'exists', autospec=True,
                                 side_effect=lambda p, m=marker: bool(m) and str(p).endswith(m)), \
                    patch('app_installer.install.run') as run, \
                    patch('app_installer.install.secrets.provision') as provision:
                with self.assertRaisesRegex(RuntimeError, 'install only supports a single-host deployment'):
                    install.install('/nonexistent-project')
                run.assert_not_called()
                provision.assert_not_called()

    def test_install_goes_ahead_on_a_single_host(self):
        with patch('app_installer.install.exists', return_value=False), \
                patch.object(Path, 'exists', autospec=True, return_value=False):
            install.require_single_host('install')

    def test_legacy_quadlets_refused_before_writes(self):
        for name in install.LEGACY:
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                (directory / (name + '.container')).touch()
                with patch('app_installer.install.exists', return_value=False), \
                        patch('app_installer.install.run', return_value=
                              subprocess.CompletedProcess([], 0, '--no-pod-prefix', '')) as run:
                    with self.assertRaisesRegex(RuntimeError, 'does not migrate'):
                        install.install(ROOT, quadlet_dir=directory)
                    self.assertEqual(run.call_count, 1)
                    self.assertEqual(len(list(directory.iterdir())), 1)

    def test_readiness_retries_are_bounded(self):
        for path, attempts, delay, status in [('/health', 30, 1, 'ok'),
                                             ('/ready', 30, 1, 'ready'),
                                             ('/discovery', 90, 2, None)]:
            with patch.object(keycloak, 'request', side_effect=TimeoutError), \
                    patch('app_installer.keycloak.time.sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError, str(attempts)):
                    keycloak.wait(path, attempts, delay, status)
                self.assertEqual(sleep.call_count, attempts - 1)
                self.assertTrue(all(call.args == (delay,) for call in sleep.call_args_list))
