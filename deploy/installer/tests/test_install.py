import contextlib
import dataclasses
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.platform_fixture import PLATFORM_FILE, fixture_platform  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offline_bundle  # noqa: E402
from app_installer import (  # noqa: E402
    apps,
    checks,
    cli,
    install,
    keycloak,
    kube_play,
    secrets,
    settings,
    target_render,
    uninstall,
)
from fake_host import FakeHost, RenderingHost, write_images  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def copy_project(root):
    """The parts of the repository a build-mode install reads, copied into root."""
    for part in ('examples', 'deploy/quadlet'):
        shutil.copytree(ROOT / part, root / part)
    (root / 'platform.yaml').write_text(PLATFORM_FILE.read_text().replace('../../examples/', 'examples/'))
    return root


class InstallTests(unittest.TestCase):
    def test_an_anonymous_check_can_expect_401(self):
        platform = fixture_platform().select(['notes'])
        app = dataclasses.replace(platform.apps[0], checks=(apps.Check('/api/notes', 401),))
        platform = dataclasses.replace(platform, apps=(app,))
        with FakeHost(platform=platform) as host:
            checks.verify(platform, {'notes': app.hostname})
        self.assertIn((app.hostname, '/api/notes'), host.requests)

    def exercise_install(self, mode, repeat=False, source_override=None, applications=None):
        """Install in server mode from a real offline bundle, or in dev mode by building; return the calls."""
        applications = (fixture_platform().apps[0],) if applications is None else applications
        platform = fixture_platform().select([app.name for app in applications])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'quadlet'
            runtime = directory / 'platform-kube-runtime'
            if mode == 'server':
                offline_bundle.build(root, applications)
                project, how, Host = ROOT, dict(deployment_mode='offline', bundle_directory=root), FakeHost
            else:
                project, how, Host = copy_project(root), dict(deployment_mode='build'), RenderingHost

            with Host(unit_directory=runtime, source=source_override, platform=platform) as host, \
                    patch.object(keycloak, 'configure') as configure, \
                    patch.object(settings, 'DEV_STATE_FILE', root / 'app-installer-dev.json'):
                calls = host.calls
                install.install(project, mode=mode, quadlet_dir=directory, platform=platform, **how)
                configure.assert_called_once_with('fixture-password', [
                    (app.login_client, app.hostname, app.login_audience) for app in platform.login_apps])
                # Each app ready, then its checks, through nginx with its hostname (checks.verify).
                self.assertEqual(host.requests, [(app.hostname, path) for app in applications
                                                 for path in (app.ready, *(check.path for check in app.checks))])
                if mode == 'server':
                    # Each app's pod, each database app's PostgreSQL, Keycloak, its PostgreSQL and nginx.
                    pods = len(applications) + len(platform.database_apps) + 3
                    self.assertEqual(len(list(runtime.glob('*.kube'))), pods)
                    self.assertEqual(len(host.ran('systemctl', '--user', 'show')), pods)
                else:
                    self.assertFalse(directory.exists())
                if repeat:
                    calls.clear()
                    install.install(project, mode=mode, quadlet_dir=directory, platform=platform, **how)
                    self.assertFalse(host.ran('systemctl', '--user', 'stop'))
            # database_roles.setup: once per app with a database, after its database is healthy,
            # through the database's own psql; the SQL and its passwords never in argv.
            bootstrap = [i for i, a in enumerate(calls) if a[:3] == ['podman', 'exec', '--interactive']
                         and '--quiet' in a]
            self.assertEqual(len(bootstrap), len(platform.database_apps))
            wait = next(i for i, a in enumerate(calls) if a[:2] == ['podman', 'wait'])
            self.assertLess(wait, bootstrap[0])
            for index in bootstrap:
                self.assertFalse([word for word in calls[index] if 'PASSWORD' in word.upper()])
            return calls, bootstrap

    def test_every_pod_server_and_repeat(self):
        calls, bootstrap = self.exercise_install('server', applications=fixture_platform().apps, repeat=True)
        # The repeated install makes each app's roles again, once, from the role passwords' secrets.
        for app in fixture_platform().database_apps:
            setup = [calls[i] for i in bootstrap if app.database.container in calls[i]]
            self.assertEqual(len(setup), 1)
            for role in ('migrator', 'app'):
                self.assertIn(['podman', 'secret', 'inspect', '--showsecret', '--format', '{{.SecretData}}',
                               app.database.secret(role)], calls)
        for service in ('keycloak', 'keycloak-postgres', 'shared-proxy'):
            self.assertEqual(calls.count(['systemctl', '--user', 'start', service + '.service']), 1)

    def test_every_pod_dev_order(self):
        calls, bootstrap = self.exercise_install('dev', applications=fixture_platform().apps)
        plays = [(i, a) for i, a in enumerate(calls)
                 if a[:3] == ['podman', 'kube', 'play'] and '--help' not in a]
        self.assertEqual([Path(a[-1]).stem for _, a in plays], [
            'todo-postgres', 'notes-postgres', 'keycloak-postgres', 'keycloak', 'todo-app', 'notes-app', 'help-app',
            'shared-proxy'])
        # Todo's roles before Notes' database plays, Notes' before Keycloak's; none after the apps.
        self.assertLess(bootstrap[0], plays[1][0])
        self.assertLess(bootstrap[1], plays[2][0])
        self.assertLess(bootstrap[-1], plays[3][0])

    def test_server_install(self):
        calls, bootstrap = self.exercise_install('server')
        app = calls.index(['systemctl', '--user', 'start', 'todo-app.service'])
        # The roles are made before the app starts, and not again after it.
        self.assertEqual(len(bootstrap), 1)
        self.assertLess(bootstrap[0], app)

    def test_server_repeat_does_not_restart_unchanged_workloads(self):
        self.exercise_install('server', repeat=True)

    def test_server_repeat_only_restarts_the_apps_whose_manifests_changed(self):
        applications = fixture_platform().apps
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'quadlet'
            offline_bundle.build(root, applications)

            with FakeHost(unit_directory=directory / 'platform-kube-runtime') as host, \
                    patch.object(keycloak, 'configure'), \
                    patch.object(settings, 'DEV_STATE_FILE', root / 'app-installer-dev.json'):
                install.install(ROOT, mode='server', deployment_mode='offline',
                                bundle_directory=root, quadlet_dir=directory, platform=fixture_platform().select([a.name for a in applications]))
                changed = root / 'generated/target/manifests' / fixture_platform().apps[0].manifest
                changed.write_text(changed.read_text() + '# changed\n')
                host.calls.clear()
                install.install(ROOT, mode='server', deployment_mode='offline',
                                bundle_directory=root, quadlet_dir=directory, platform=fixture_platform().select([a.name for a in applications]))
            stopped = {a[3] for a in host.ran('systemctl', '--user', 'stop')}
            self.assertEqual(stopped, {'todo-app.service'})

    def test_without_login_keycloak_is_neither_installed_nor_started(self):
        wiki = apps.App(name='wiki', hostname='wiki.test', replication_port=5441,
                        images=(apps.AppImage(name='backend', context='.'), apps.AppImage(name='frontend', context='.')))
        platform = apps.Platform(apps=(wiki,), identity_hostname='auth.test')
        installed, started, roles = [], [], []
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(install.workloads, 'install_postgres',
                             lambda *a, database, target: installed.append(database.container)), \
                patch.object(install.workloads, 'install_application', lambda *a, app, target: installed.append(app.pod)), \
                patch.object(install.workloads, 'install_keycloak', lambda *a, target: installed.append('keycloak')), \
                patch.object(install.workloads, 'install_shared_proxy',
                             lambda *a, platform, target: installed.append('shared-proxy')), \
                patch.object(install.quadlet, 'systemctl', lambda *a: started.append(a[1])), \
                patch.object(install, 'run'), patch.object(install.database_roles, 'setup', roles.append):
            plan = install.Plan(platform=platform, environment=None, port=8443, root=ROOT, directory=Path(temp),
                                runtime=Path(temp), target=None, mode='server', deployment_mode='build',
                                bundle_directory='', refresh_images=False, publish_address='127.0.0.1')
            prepared = install.Prepared(rendered_directory=None, image_changes={'wiki': {'backend': False, 'frontend': False}},
                                        shared_images={'proxy': False}, hostnames={}, identity='auth.test',
                                        tls_changed=False)
            install.write_definitions(plan, prepared)
            install.start_in_order(platform, set())
        self.assertEqual(installed, ['wiki-postgres', 'wiki-app', 'shared-proxy'])
        self.assertEqual(started, ['wiki-postgres.service', 'wiki-app.service', 'shared-proxy.service'])
        self.assertEqual(roles, [wiki])

    def test_a_pod_that_breaks_the_rules_is_refused_before_the_host_records_it(self):
        # Help's pod has no container port 9000, so this endpoint breaks the pod contract.
        help_ = apps.App(name='help', hostname='help.test', has_database=False, replication_port=0,
                         images=(apps.AppImage(name='site', context='examples/help'),),
                         endpoints=(apps.Endpoint(name='site', port=9000),),
                         routes=(apps.Route(path='/', to='site'),), pod_template='examples/help/pod.yaml.j2')
        for platform, message in (
                (apps.Platform(apps=(help_,), identity_hostname='auth.test'), 'no container port 9000'),):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp, \
                    FakeHost(unit_directory=Path(temp) / 'quadlet/platform-kube-runtime', platform=platform) as host:
                with self.assertRaisesRegex(ValueError, message):
                    install.install(ROOT, quadlet_dir=Path(temp) / 'quadlet', platform=platform)
                # Only read-only checks of Podman ran.
                self.assertTrue(all(call in (['podman', 'kube', 'play', '--help'], ['podman', '--version'])
                                    for call in host.calls), host.calls)
                self.assertEqual(host.platform_record.read_bytes(), target_render.platform_record(host.platform))

    def test_a_platform_that_leaves_out_an_installed_workload_is_refused(self):
        # The host recorded todo, notes and help; removing a workload is not built (plan, 5.8 and phase 7).
        checkout = fixture_platform()
        without_login = [dataclasses.replace(app, login_client='', login_audience='') for app in checkout.apps]
        for platform, left_out in (
                (checkout.select(['todo']), 'help-app.service, notes-app.service, notes-postgres.service'),
                # The same apps, but Todo without its database, or no app with login.
                (dataclasses.replace(checkout, apps=(dataclasses.replace(checkout.apps[0], has_database=False,
                                                                         replication_port=0),
                                                     *checkout.apps[1:])), 'todo-postgres.service'),
                (dataclasses.replace(checkout, apps=tuple(without_login)), 'keycloak.service, keycloak-postgres.service')):
            with self.subTest(left_out=left_out), tempfile.TemporaryDirectory() as temp, \
                    FakeHost(unit_directory=Path(temp) / 'quadlet/platform-kube-runtime') as host:
                with self.assertRaisesRegex(ValueError, f'This host may run {left_out} .* which this platform '
                                                        'leaves out.* build and deliver a bundle'):
                    install.install(ROOT, quadlet_dir=Path(temp) / 'quadlet', platform=platform)
                self.assertEqual(host.platform_record.read_bytes(), target_render.platform_record(checkout))
                # Only read-only checks ran (require_single_host asks whether DR secrets exist).
                self.assertTrue(all(call[1:3] in (['secret', 'exists'], ['kube', 'play']) for call in host.calls),
                                host.calls)

    def test_a_record_from_another_installer_is_left_by_removing_it_and_the_data_stays(self):
        # A host installed before the record had this format: a plain uninstall with that installer kept
        # its databases, secrets and record. The message names the record; once it is removed, the install
        # takes the host over with the same secrets and volumes.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            offline_bundle.build(root, fixture_platform().apps)
            directory = root / 'quadlet'
            with FakeHost(unit_directory=directory / 'platform-kube-runtime') as host, \
                    patch.object(keycloak, 'configure'):
                host.platform_record.write_text(json.dumps(host.platform.to_json()))
                kept = dict(host.secrets)
                arguments = dict(mode='server', deployment_mode='offline', bundle_directory=root, quadlet_dir=directory)
                with self.assertRaisesRegex(target_render.TargetError,
                                            f'without removing data .* then remove {host.platform_record}'):
                    install.install(ROOT, **arguments)
                host.platform_record.unlink()
                install.install(ROOT, **arguments)
                self.assertEqual({name: host.secrets[name] for name in kept}, kept)
                self.assertEqual(host.ran('podman', 'secret', 'rm') + host.ran('podman', 'volume', 'rm'), [])
                self.assertEqual(host.platform_record.read_bytes(), target_render.platform_record(host.platform))

    def test_a_static_app_alone_installs_without_postgres_or_keycloak(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / 'project'
            for part in ('examples', 'deploy/manifests', 'deploy/quadlet'):
                shutil.copytree(ROOT / part, project / part)
            (project / 'platform.yaml').write_text(
                'identityHostname: auth.test\npublicPort: 8443\nlogLevel: info\n'
                'environments: {local: {}, prod: {}}\n'
                'apps:\n  - path: examples/help\n    hostname: help.test\n')
            from app_installer import bundle
            data = bundle.build(project, 'prod', root / 'bundle')
            platform = apps.Platform.from_json(data['platform'])
            write_images(root / 'bundle', platform)
            self.assertEqual([(w.pod, w.requires) for w in platform.workloads()],
                             [('help-app', ()), ('shared-proxy', ('help-app.service',))])
            directory = root / 'quadlet'
            runtime = directory / 'platform-kube-runtime'
            with FakeHost(unit_directory=runtime, platform=platform) as host, \
                    patch.object(keycloak, 'configure') as configure:
                self.assertTrue(install.install(ROOT, mode='server', deployment_mode='offline',
                                                bundle_directory=root / 'bundle', quadlet_dir=directory))
            configure.assert_not_called()
            self.assertEqual(sorted(path.name for path in runtime.glob('*.kube')), ['help-app.kube', 'shared-proxy.kube'])
            self.assertEqual(host.requests, [('help.test', '/'), ('help.test', '/')])
            self.assertFalse([call for call in host.calls if '--quiet' in call or call[:2] == ['podman', 'wait']])
            # Only nginx's TLS secrets: no database password, no Keycloak admin.
            self.assertTrue(all(call[3].startswith('platform-') for call in host.ran('podman', 'secret', 'create')))
            self.assertEqual([call[3] for call in host.ran('systemctl', '--user', 'start')],
                             ['help-app.service', 'shared-proxy.service'])

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
        self.assertEqual(len(bootstrap), 1)
        self.assertIn('127.0.0.1:8080:8080', plays[-1][1])
        self.assertIn('127.0.0.1:8443:8443', plays[-1][1])
        self.assertFalse(any(a[0] == 'systemctl' for a in calls))

    def test_dev_repeat_preserves_pods_and_manifest_changes_reapply(self):
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
                    patch.object(kube_play.database_roles, 'setup') as roles:
                self.assertTrue(kube_play.up(directory, fixture_platform().select(['todo']), state))
                self.assertEqual(roles.call_count, 1)
                command.reset_mock()
                roles.reset_mock()
                self.assertFalse(kube_play.up(directory, fixture_platform().select(['todo']), state))
                roles.assert_not_called()
                self.assertTrue(all(call.args[:3] == ('podman', 'pod', 'inspect')
                                    for call in command.call_args_list))
                (directory / 'todo-config.yaml').write_text('changed: true')
                self.assertTrue(kube_play.up(directory, fixture_platform().select(['todo']), state))
                downs = [call.kwargs['input'] for call in command.call_args_list
                         if '--down' in call.args]
                self.assertEqual(downs, ['fixture: ' + name for name in (
                    'shared-proxy', 'todo-app', 'keycloak', 'keycloak-postgres', 'todo-postgres')])
                self.assertEqual(roles.call_count, 1)

    def test_down_uses_reverse_order_and_only_existing_files(self):
        with tempfile.TemporaryDirectory() as temp, patch('app_installer.kube_play.run') as run, \
                patch.object(secrets, 'remove_kube_volumes'):
            root = Path(temp)
            for name in ('shared-proxy', 'todo-app', 'todo-postgres'):
                (root / (name + '.yaml')).touch()
            state = root / '.state.json'
            self.assertTrue(kube_play.down(root, fixture_platform(), state_file=state))
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
            self.assertTrue(kube_play.down(root / 'rendered-again', fixture_platform(), state_file=state))
            run.assert_called_once_with('podman', 'kube', 'play', '--down', '-',
                                        input='kind: Pod # notes-app')
            self.assertFalse(state.exists())

    def test_down_reports_when_nothing_was_installed(self):
        with tempfile.TemporaryDirectory() as temp, patch('app_installer.kube_play.run') as run, \
                patch.object(secrets, 'remove_kube_volumes'):
            root = Path(temp)
            self.assertFalse(kube_play.down(root, fixture_platform(), state_file=root / '.state.json'))
            run.assert_not_called()

    def test_down_finds_the_default_state_file_up_actually_wrote(self):
        # up() and down() must agree on the state file's location without a
        # caller passing state_file explicitly, the way the CLI's plain
        # `install --mode dev` and `down` commands actually call them.
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(settings, 'DEV_STATE_FILE', Path(temp) / 'app-installer-dev.json'), \
                patch('app_installer.kube_play.exists', return_value=False), \
                patch('app_installer.kube_play.database_roles.setup'), \
                patch('app_installer.kube_play.run',
                      return_value=subprocess.CompletedProcess([], 0, 'Running', '')):
            directory = Path(temp) / 'rendered'
            directory.mkdir()
            for name in ('todo-postgres', 'todo-config', 'todo-app', 'keycloak-postgres', 'keycloak-config',
                        'keycloak', 'shared-proxy'):
                (directory / (name + '.yaml')).write_text('fixture: ' + name)
            self.assertTrue(kube_play.up(directory, fixture_platform().select(['todo'])))
            self.assertTrue(settings.DEV_STATE_FILE.is_file())

            with patch('app_installer.kube_play.run') as run, patch.object(secrets, 'remove_kube_volumes'):
                # A different directory argument: down must still find the
                # pods through the recorded state file, not through this one.
                self.assertTrue(kube_play.down(Path(temp) / 'unrelated', fixture_platform()))
                run.assert_called()
            self.assertFalse(settings.DEV_STATE_FILE.is_file())

    def test_every_secret_is_generated_without_a_terminal(self):
        # No secret should ever require an interactive prompt: provision() must
        # work unattended (CI, scripted installs) for every one of them, not
        # just the migrator/app role passwords.
        with patch('app_installer.secrets.exists', return_value=False), \
                patch('sys.stdin.isatty', return_value=False), \
                patch('app_installer.secrets.run') as run:
            secrets.provision(fixture_platform())
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
            secrets.provision(fixture_platform())
            self.assertEqual(run.call_count, len(missing))
            for call in run.call_args_list:
                self.assertRegex(call.kwargs['input'], r'^[a-zA-Z0-9]{32}$')

    class AdminApi:
        """Keycloak's admin API for keycloak.configure: the realm, clients by ID and their mappers."""

        ISSUER = {'issuer': 'https://auth.test:8443/auth/realms/todo'}

        def __init__(self, clients=(), mappers=None):
            self.clients = {client['clientId']: dict(client) for client in clients}
            self.mappers = {client_id: list(found) for client_id, found in (mappers or {}).items()}
            self.calls = []

        def request(self, path, method='GET', data=None, token=None, form=False, hostname=None):
            self.calls.append((method, path, data))
            if path == keycloak.ADMIN_TOKEN:
                return {'access_token': 'token'}
            if path == keycloak.ADMIN_REALM:
                return dict(keycloak.REALM_SECURITY)
            if path.startswith(keycloak.ADMIN_REALM + '/clients?'):
                client_id = path.split('clientId=')[1]
                return [{'id': client_id}] if client_id in self.clients else []
            if path == keycloak.ADMIN_REALM + '/clients' and method == 'POST':
                self.clients[data['clientId']] = data
                return {}
            client_id, _, rest = path.removeprefix(keycloak.ADMIN_REALM + '/clients/').partition('/')
            if not rest:
                return dict(self.clients[client_id]) if method == 'GET' else {}
            if method == 'GET':
                return list(self.mappers.get(client_id, []))
            return {}

        def writes(self):
            return [(method, path.removeprefix(keycloak.ADMIN_REALM), data) for method, path, data in self.calls
                    if method != 'GET' and path != keycloak.ADMIN_TOKEN]

    def configure(self, api, clients):
        with patch.object(keycloak, 'wait', return_value=self.AdminApi.ISSUER), \
                patch.object(keycloak, 'request', side_effect=api.request):
            return keycloak.configure('password', clients)

    def test_a_missing_client_is_made_from_the_platforms_defaults(self):
        api = self.AdminApi()
        self.assertTrue(self.configure(api, [('notes-frontend', 'notes.test', 'notes-api')]))
        [(method, path, client)] = api.writes()
        self.assertEqual((method, path), ('POST', '/clients'))
        self.assertEqual(client, {
            'clientId': 'notes-frontend', 'name': 'notes-frontend', 'enabled': True, 'protocol': 'openid-connect',
            'publicClient': True, 'standardFlowEnabled': True, 'implicitFlowEnabled': False,
            'directAccessGrantsEnabled': False, 'serviceAccountsEnabled': False,
            'redirectUris': ['https://notes.test:8443/'], 'webOrigins': ['https://notes.test:8443'],
            'attributes': {'pkce.code.challenge.method': 'S256'},
            # The API audience in access tokens only: an ID token names the client, never notes-api.
            'protocolMappers': [{'name': 'api audience', 'protocol': 'openid-connect',
                                 'protocolMapper': 'oidc-audience-mapper', 'consentRequired': False,
                                 'config': {'included.custom.audience': 'notes-api',
                                            'access.token.claim': 'true', 'id.token.claim': 'false'}}]})

    def test_an_existing_client_gets_its_owned_fields_and_audience_corrected_and_keeps_the_rest(self):
        client = {'clientId': 'todo-frontend', 'other-setting': True, 'redirectUris': [], 'webOrigins': [],
                  'publicClient': True, 'standardFlowEnabled': True, 'directAccessGrantsEnabled': True,
                  'attributes': {'pkce.code.challenge.method': '', 'operator': 'kept'},
                  'protocolMappers': [{'id': 'old'}]}
        mappers = [{'id': 'old', 'protocolMapper': 'oidc-audience-mapper',
                    'config': {'included.client.audience': 'todo-frontend', 'access.token.claim': 'true'}},
                   # Another audience would let a Todo token pass Notes' backend: it goes too.
                   {'id': 'extra', 'protocolMapper': 'oidc-audience-mapper',
                    'config': {'included.custom.audience': 'notes-api', 'access.token.claim': 'true'}},
                   {'id': 'kept', 'protocolMapper': 'oidc-usermodel-attribute-mapper', 'config': {}}]
        api = self.AdminApi([client], {'todo-frontend': mappers})
        self.assertTrue(self.configure(api, [('todo-frontend', 'todo.test', 'todo-api')]))
        writes = api.writes()
        self.assertEqual([(method, path) for method, path, _ in writes], [
            ('PUT', '/clients/todo-frontend'),
            ('DELETE', '/clients/todo-frontend/protocol-mappers/models/old'),
            ('DELETE', '/clients/todo-frontend/protocol-mappers/models/extra'),
            ('POST', '/clients/todo-frontend/protocol-mappers/models')])
        corrected = writes[0][2]
        self.assertEqual({key: corrected[key] for key in ('redirectUris', 'webOrigins', 'directAccessGrantsEnabled',
                                                          'implicitFlowEnabled', 'other-setting', 'attributes')},
                         {'redirectUris': ['https://todo.test:8443/'], 'webOrigins': ['https://todo.test:8443'],
                          'directAccessGrantsEnabled': False, 'implicitFlowEnabled': False, 'other-setting': True,
                          'attributes': {'pkce.code.challenge.method': 'S256', 'operator': 'kept'}})
        self.assertNotIn('protocolMappers', corrected)
        self.assertEqual(writes[-1][2], keycloak.audience_mapper('todo-api'))

    def test_an_audience_mapper_that_also_names_a_client_audience_is_replaced(self):
        # Keycloak prefers included.client.audience to the custom one: right custom audience or not,
        # such a mapper gives tokens the client's audience, so the platform replaces it.
        origin = 'https://todo.test:8443'
        client = {'clientId': 'todo-frontend', **keycloak.owned_client_fields(origin),
                  'attributes': {'pkce.code.challenge.method': 'S256'}}
        mapper = keycloak.audience_mapper('todo-api')
        mapper = {**mapper, 'id': 'both', 'config': {**mapper['config'], 'included.client.audience': 'todo-frontend'}}
        api = self.AdminApi([client], {'todo-frontend': [mapper]})
        self.assertTrue(self.configure(api, [('todo-frontend', 'todo.test', 'todo-api')]))
        self.assertEqual([(method, path) for method, path, _ in api.writes()], [
            ('DELETE', '/clients/todo-frontend/protocol-mappers/models/both'),
            ('POST', '/clients/todo-frontend/protocol-mappers/models')])

    def test_a_client_as_the_platform_defines_it_is_left_alone(self):
        origin = 'https://todo.test:8443'
        client = {'clientId': 'todo-frontend', 'operator': 'kept', **keycloak.owned_client_fields(origin),
                  'attributes': {'pkce.code.challenge.method': 'S256'}}
        mapper = keycloak.audience_mapper('todo-api')
        # Keycloak adds config keys of its own; only the platform's are compared.
        mapper = {**mapper, 'id': 'm', 'config': {**mapper['config'], 'introspection.token.claim': 'true'}}
        api = self.AdminApi([client], {'todo-frontend': [mapper]})
        self.assertFalse(self.configure(api, [('todo-frontend', 'todo.test', 'todo-api')]))
        self.assertEqual(api.writes(), [])

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

    def test_realm_import_carries_the_same_login_protection_and_no_client(self):
        realm = json.loads((Path(__file__).resolve().parents[3] / 'keycloak/todo-realm.json').read_text())
        self.assertEqual({key: realm.get(key) for key in keycloak.REALM_SECURITY}, keycloak.REALM_SECURITY)
        # Every app's client comes from keycloak.configure, from the platform's defaults.
        self.assertNotIn('clients', realm)

    def test_readiness_retries_connection_reset_during_proxy_startup(self):
        with patch.object(keycloak, 'request', side_effect=[ConnectionResetError(), {'status': 'ok'}]), \
                patch('app_installer.keycloak.time.sleep') as sleep:
            self.assertEqual(keycloak.wait('/health', 30, 1, 'ok'), {'status': 'ok'})
            sleep.assert_called_once_with(1)

    def test_keycloak_rejects_non_https_issuer_before_token_request(self):
        with patch.object(keycloak, 'wait', return_value={'issuer': 'http://todo/auth/realms/todo'}), \
                patch.object(keycloak, 'request') as request:
            with self.assertRaisesRegex(RuntimeError, 'HTTPS issuer'):
                keycloak.configure('password', [('todo-frontend', 'todo.test', 'todo-api')])
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
                uninstall.uninstall(fixture_platform())
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
                for name in uninstall.quadlet_files(fixture_platform()):
                    (directory / name).touch()
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertTrue(uninstall.uninstall(fixture_platform(), remove_data, directory))
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
                for name in (fixture_platform().apps[1].kube_secret('backend'), apps.KEYCLOAK_DATABASE.kube_secret,
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
            self.assertTrue(uninstall.uninstall(fixture_platform(), quadlet_dir=Path(temp)))
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
            self.assertFalse(uninstall.uninstall(fixture_platform(), quadlet_dir=directory))

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
            uninstall.uninstall(fixture_platform(), quadlet_dir=directory)
            commands = [call.args[0] for call in run.call_args_list]
            removals = [command for command in commands if command[:3] == ['podman', 'pod', 'rm']]
            self.assertEqual([command[-1] for command in removals],
                             ['shared-proxy', 'help-app', 'notes-app', 'todo-app', 'keycloak', 'keycloak-postgres',
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
            uninstall.uninstall(fixture_platform(), quadlet_dir=directory, **options)
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
                uninstall.uninstall(fixture_platform(), remove_backups=True)
            run.assert_not_called()
            checks.assert_not_called()

    def test_remove_backups_still_refuses_a_dr_host(self):
        with patch('app_installer.install.exists', return_value=True), \
                patch('app_installer.uninstall.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'single-host deployment'):
                uninstall.uninstall(fixture_platform(), remove_data=True, remove_backups=True)
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
                uninstall.uninstall(fixture_platform(), quadlet_dir=directory)
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
                    uninstall.uninstall(fixture_platform())
                run.assert_not_called()

    def test_install_refuses_a_replicated_host_before_any_command(self):
        # Rerunning install on a replicated primary would drop the LAN publication
        # of its databases and cut off the standby (seen in an acceptance run).
        markers = ('.config/platform/todo-standby-entrypoint.sh', '/opt/platform/bin/app_dr.py',
                   '/opt/platform/bin/app_backup.py', '/opt/platform/bin/todo_dr.py', '/opt/platform/bin/todo_backup.py')
        cases = [(marker, lambda kind, name: False) for marker in markers]
        cases += [(None, lambda kind, name, d=d: name == d.secret('replicator'))
                  for d in fixture_platform().replicated_databases]
        for marker, secret in cases:
            with self.subTest(marker=marker), patch('app_installer.install.exists', side_effect=secret), \
                    patch.object(Path, 'exists', autospec=True,
                                 side_effect=lambda p, m=marker: bool(m) and str(p).endswith(m)), \
                    patch('app_installer.install.run') as run, \
                    patch('app_installer.install.secrets.provision') as provision:
                with self.assertRaisesRegex(RuntimeError, 'install only supports a single-host deployment'):
                    install.install(ROOT, platform=fixture_platform())
                run.assert_not_called()
                provision.assert_not_called()

    def test_install_goes_ahead_on_a_single_host(self):
        with patch('app_installer.install.exists', return_value=False), \
                patch.object(Path, 'exists', autospec=True, return_value=False):
            install.require_single_host('install', fixture_platform())

    def test_legacy_quadlets_refused_before_writes(self):
        for name in install.legacy_units(fixture_platform()):
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
