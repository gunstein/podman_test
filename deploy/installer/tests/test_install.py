import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from todo_installer import install, keycloak, kube_play, secrets, uninstall  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class InstallTests(unittest.TestCase):
    def exercise_install(self, mode, repeat=False, source_override=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'quadlet'
            runtime = directory / 'todo-kube-runtime'
            rendered = root / 'generated/kube-runtime'
            rendered.mkdir(parents=True)
            for name in ('postgres', 'app', 'keycloak', 'shared-proxy', 'config'):
                (rendered / (name + '.yaml')).write_text('fixture: true\n')
            calls = []

            def command(argv, **kwargs):
                calls.append(argv)
                stdout = ''
                if argv == ['podman', 'kube', 'play', '--help']:
                    stdout = '--no-pod-prefix'
                elif argv[:3] == ['podman', 'image', 'inspect']:
                    stdout = '[{"Labels":{"io.todo.proxy":"nginx"}}]'
                elif argv[:3] == ['podman', 'secret', 'inspect']:
                    stdout = 'fixture-password\n'
                elif argv[:3] == ['systemctl', '--user', 'show']:
                    stdout = (source_override or str(runtime / argv[3].replace('.service', '.kube'))) + '\n'
                rc = 1 if argv[:3] == ['podman', 'pod', 'exists'] else 0
                return subprocess.CompletedProcess(argv, rc, stdout, '')

            with patch('subprocess.run', side_effect=command), \
                    patch.object(keycloak, 'configure') as configure:
                install.install(ROOT, mode=mode, deployment_mode='offline',
                                bundle_directory=root, quadlet_dir=directory)
                if mode == 'server':
                    configure.assert_called_once_with('fixture-password', [('todo-frontend', 'todo.test')])
                    self.assertEqual(len(list(runtime.glob('*.kube'))), 4)
                    self.assertEqual(len([a for a in calls if a[:3] == ['systemctl', '--user', 'show']]), 4)
                else:
                    self.assertFalse(directory.exists())
                    configure.assert_called_once_with('fixture-password', [('todo-frontend', 'todo.test')])
                if repeat:
                    calls.clear()
                    install.install(ROOT, mode=mode, deployment_mode='offline',
                                    bundle_directory=root, quadlet_dir=directory)
                    self.assertFalse(any(a[:3] == ['systemctl', '--user', 'stop'] for a in calls))
            bootstrap = [i for i, a in enumerate(calls) if a[-1] == 'backend.setup_roles']
            self.assertEqual(len(bootstrap), 2)
            wait = next(i for i, a in enumerate(calls) if a[:2] == ['podman', 'wait'])
            self.assertLess(wait, bootstrap[0])
            for index in bootstrap:
                self.assertIn('--cap-drop', calls[index])
                self.assertIn('no-new-privileges', calls[index])
            return calls, bootstrap

    def test_server_install(self):
        calls, bootstrap = self.exercise_install('server')
        app = calls.index(['systemctl', '--user', 'start', 'todo-app.service'])
        self.assertLess(bootstrap[0], app)
        self.assertLess(app, bootstrap[1])
        self.assertLess(bootstrap[1], calls.index(['systemctl', '--user', 'start', 'shared-proxy.service']))

    def test_server_repeat_does_not_restart_unchanged_workloads(self):
        self.exercise_install('server', repeat=True)

    def test_source_path_must_be_the_expected_workload_unit(self):
        for source in ('/tmp/todo-app.container', '/tmp/todo-app.kube',
                       '/tmp/todo-kube-runtime/unrelated.kube'):
            with self.subTest(source=source), self.assertRaisesRegex(RuntimeError, 'SourcePath'):
                self.exercise_install('server', source_override=source)

    def test_dev_install_order_and_publication(self):
        calls, bootstrap = self.exercise_install('dev')
        plays = [(i, a) for i, a in enumerate(calls)
                 if a[:3] == ['podman', 'kube', 'play'] and '--help' not in a]
        self.assertEqual([Path(a[-1]).stem for _, a in plays],
                         ['postgres', 'keycloak', 'app', 'shared-proxy'])
        self.assertLess(plays[0][0], bootstrap[0])
        self.assertLess(bootstrap[0], plays[1][0])
        self.assertLess(plays[-1][0], bootstrap[1])
        self.assertIn('127.0.0.1:8080:8080', plays[-1][1])
        self.assertIn('127.0.0.1:8443:8443', plays[-1][1])
        self.assertFalse(any(a[0] == 'systemctl' for a in calls))

    def test_dev_repeat_preserves_pods_and_manifest_changes_reapply(self):
        from todo_installer.apps import APPS
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in ('app', 'postgres', 'config', 'keycloak', 'shared-proxy'):
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
                self.assertTrue(kube_play.up(directory, APPS, state))
                self.assertEqual(roles.call_count, 2)
                command.reset_mock()
                roles.reset_mock()
                self.assertFalse(kube_play.up(directory, APPS, state))
                roles.assert_not_called()
                self.assertTrue(all(call.args[:3] == ('podman', 'pod', 'inspect')
                                    for call in command.call_args_list))
                (directory / 'config.yaml').write_text('changed: true')
                self.assertTrue(kube_play.up(directory, APPS, state))
                downs = [Path(call.args[-1]).stem for call in command.call_args_list
                         if '--down' in call.args]
                self.assertEqual(downs, ['shared-proxy', 'app', 'keycloak', 'postgres'])
                self.assertEqual(roles.call_count, 2)

    def test_down_uses_reverse_order_and_only_existing_files(self):
        with tempfile.TemporaryDirectory() as temp, patch('todo_installer.kube_play.run') as run:
            root = Path(temp)
            for name in ('shared-proxy', 'app', 'postgres'):
                (root / (name + '.yaml')).touch()
            kube_play.down(root)
            self.assertEqual([c.args for c in run.call_args_list], [
                ('podman', 'kube', 'play', '--down', root / (name + '.yaml'))
                for name in ('shared-proxy', 'app', 'postgres')])

    def test_missing_admin_secret_requires_tty(self):
        with patch('todo_installer.secrets.exists', return_value=False), \
                patch('sys.stdin.isatty', return_value=False), patch('getpass.getpass') as prompt:
            with self.assertRaisesRegex(RuntimeError, 'interactive terminal'):
                secrets.provision()
            prompt.assert_not_called()

    def test_generated_secrets_are_alphanumeric_and_existing_secrets_preserved(self):
        missing = {'todo-migrator-password', 'todo-app-password', 'todo-keycloak-db-password'}
        with patch('todo_installer.secrets.exists', side_effect=lambda _, name: name not in missing), \
                patch('todo_installer.secrets.run') as run:
            secrets.provision()
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                self.assertRegex(call.kwargs['input'], r'^[a-zA-Z0-9]{32}$')

    def test_keycloak_uses_issuer_and_preserves_client_fields(self):
        for unchanged in (False, True):
            client = {'id': 'client', 'other-setting': True, 'redirectUris': [], 'webOrigins': []}
            if unchanged:
                client.update(redirectUris=['https://todo.test:8443/'],
                              webOrigins=['https://todo.test:8443'])
            with patch.object(keycloak, 'wait', side_effect=[{}, {}, {
                    'issuer': 'https://todo.test:8443/auth/realms/todo'}]), \
                    patch.object(keycloak, 'request', side_effect=[
                        {'access_token': 'token'}, [{'id': 'client'}], client, None]) as request:
                self.assertEqual(keycloak.configure('password'), not unchanged)
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
            responses = [{'access_token': 'token'}, [{'id': 'todo-id'}], todo]
            responses += [[], None] if missing else [[{'id': 'notes-id'}], {
                'id': 'notes-id', 'custom': True, 'redirectUris': [], 'webOrigins': []}, None]
            with patch.object(keycloak, 'wait', side_effect=[{}, {}, {
                    'issuer': 'https://todo.test:8443/auth/realms/todo'}]), \
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

    def test_readiness_retries_connection_reset_during_proxy_startup(self):
        with patch.object(keycloak, 'request', side_effect=[ConnectionResetError(), {'status': 'ok'}]), \
                patch('todo_installer.keycloak.time.sleep') as sleep:
            self.assertEqual(keycloak.wait('/health', 30, 1, 'ok'), {'status': 'ok'})
            sleep.assert_called_once_with(1)

    def test_keycloak_rejects_non_https_issuer_before_token_request(self):
        with patch.object(keycloak, 'wait', return_value={'issuer': 'http://todo/auth/realms/todo'}), \
                patch.object(keycloak, 'request') as request:
            with self.assertRaisesRegex(RuntimeError, 'HTTPS issuer'):
                keycloak.configure('password')
            request.assert_not_called()


class UninstallTests(unittest.TestCase):
    def test_refuses_dr_secret_before_any_mutation(self):
        with patch('todo_installer.uninstall.exists', return_value=True), \
                patch('todo_installer.uninstall.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'single-host deployment'):
                uninstall.uninstall()
            run.assert_not_called()

    def test_data_and_secret_removal_requires_explicit_flag(self):
        for remove_data in (False, True):
            with tempfile.TemporaryDirectory() as temp, \
                    patch('todo_installer.uninstall.exists',
                          side_effect=lambda kind, name: name != 'todo-replicator-password'), \
                    patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
                directory = Path(temp)
                (directory / 'todo-kube-runtime').mkdir()
                for name in uninstall.QUADLET_FILES:
                    (directory / name).touch()
                uninstall.uninstall(remove_data, directory)
                self.assertFalse(list(directory.iterdir()))
                calls = [c.args[0] for c in run.call_args_list]
                self.assertEqual(['podman', 'volume', 'rm', 'todo-postgres-data'] in calls, remove_data)
                self.assertEqual(['podman', 'secret', 'rm', 'todo-db-password'] in calls, remove_data)
                self.assertNotIn(['podman', 'volume', 'rm', 'todo-postgres-backup'], calls)
                self.assertIn('app-network-network.service', calls[0])
                self.assertIn(['podman', 'volume', 'rm', 'todo-nginx-data'], calls)
                self.assertIn(['podman', 'volume', 'rm', 'todo-caddy-data'], calls)

    def test_unexpected_stop_failure_preserves_files(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch('todo_installer.uninstall.exists', return_value=False), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            directory = Path(temp)
            target = directory / 'app-network.network'
            target.touch()
            with self.assertRaises(RuntimeError):
                uninstall.uninstall(quadlet_dir=directory)
            self.assertTrue(target.exists())


class FailureBoundaryTests(unittest.TestCase):
    def test_each_dr_marker_refuses_uninstall(self):
        for marker in ('.config/todo/todo-standby-entrypoint.sh',
                       '/opt/todo/bin/todo_dr.py', '/opt/todo/bin/todo_backup.py'):
            with patch('todo_installer.uninstall.exists', return_value=False), \
                    patch.object(Path, 'exists', autospec=True,
                                 side_effect=lambda p: str(p).endswith(marker)), \
                    patch('todo_installer.uninstall.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'single-host deployment'):
                    uninstall.uninstall()
                run.assert_not_called()

    def test_legacy_quadlets_refused_before_writes(self):
        for name in install.LEGACY:
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                (directory / (name + '.container')).touch()
                with patch('todo_installer.install.run', return_value=
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
                    patch('todo_installer.keycloak.time.sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError, str(attempts)):
                    keycloak.wait(path, attempts, delay, status)
                self.assertEqual(sleep.call_count, attempts - 1)
                self.assertTrue(all(call.args == (delay,) for call in sleep.call_args_list))
