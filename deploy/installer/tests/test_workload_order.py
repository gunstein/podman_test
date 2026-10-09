"""The order the seven workloads start and stop in, written out literally (S5 step 0).

Every expectation here is a literal list, not one derived from the code
under test, so a refactor of how the order is computed cannot change it
unnoticed. Where commands run one after another (starts, waits, role setup,
plays, tear-downs, pod removal) the order is pinned exactly. Where one
`systemctl stop` takes several units at once, systemd orders them itself,
so only which units, and that the serving tier comes before the databases,
is pinned.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offline_bundle  # noqa: E402
from app_installer import (  # noqa: E402
    apps,
    backup,
    install,
    keycloak,
    kube_play,
    secrets,
    settings,
    uninstall,
)
from fake_host import FakeHost, RenderingHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
TODO = apps.APPS[:1]

ALL_STARTS = ['todo-postgres', 'notes-postgres', 'keycloak-postgres', 'keycloak',
              'todo-app', 'notes-app', 'shared-proxy']
TODO_STARTS = ['todo-postgres', 'keycloak-postgres', 'keycloak', 'todo-app', 'shared-proxy']
# Server mode: each start, each wait for a healthy database, each role setup, in order.
ALL_SERVER_EVENTS = [
    ('start', 'todo-postgres'), ('wait', 'todo-postgres'), ('roles', 'todo'),
    ('start', 'notes-postgres'), ('wait', 'notes-postgres'), ('roles', 'notes'),
    ('start', 'keycloak-postgres'), ('wait', 'keycloak-postgres'),
    ('start', 'keycloak'),
    ('start', 'todo-app'), ('roles', 'todo'),
    ('start', 'notes-app'), ('roles', 'notes'),
    ('start', 'shared-proxy'),
]
TODO_SERVER_EVENTS = [
    ('start', 'todo-postgres'), ('wait', 'todo-postgres'), ('roles', 'todo'),
    ('start', 'keycloak-postgres'), ('wait', 'keycloak-postgres'),
    ('start', 'keycloak'),
    ('start', 'todo-app'), ('roles', 'todo'),
    ('start', 'shared-proxy'),
]
# Development mode: each play (Kube YAML, ConfigMap), wait and role setup, in order.
ALL_DEV_EVENTS = [
    ('play', 'todo-postgres.yaml', 'todo-config.yaml'), ('wait', 'todo-postgres'), ('roles', 'todo'),
    ('play', 'notes-postgres.yaml', 'notes-config.yaml'), ('wait', 'notes-postgres'), ('roles', 'notes'),
    ('play', 'keycloak-postgres.yaml', 'keycloak-config.yaml'), ('wait', 'keycloak-postgres'),
    ('play', 'keycloak.yaml', None),
    ('play', 'todo-app.yaml', 'todo-config.yaml'),
    ('play', 'notes-app.yaml', 'notes-config.yaml'),
    ('play', 'shared-proxy.yaml', None),
    ('roles', 'todo'), ('roles', 'notes'),
]
TODO_DEV_EVENTS = [
    ('play', 'todo-postgres.yaml', 'todo-config.yaml'), ('wait', 'todo-postgres'), ('roles', 'todo'),
    ('play', 'keycloak-postgres.yaml', 'keycloak-config.yaml'), ('wait', 'keycloak-postgres'),
    ('play', 'keycloak.yaml', None),
    ('play', 'todo-app.yaml', 'todo-config.yaml'),
    ('play', 'shared-proxy.yaml', None),
    ('roles', 'todo'),
]
# Tear-down, one `podman kube play --down` after another: the reverse of the plays.
ALL_TEARDOWN = ['shared-proxy.yaml', 'notes-app.yaml', 'todo-app.yaml', 'keycloak.yaml',
                'keycloak-postgres.yaml', 'notes-postgres.yaml', 'todo-postgres.yaml']
TODO_TEARDOWN = ['shared-proxy.yaml', 'todo-app.yaml', 'keycloak.yaml', 'keycloak-postgres.yaml', 'todo-postgres.yaml']
ALL_MANIFESTS = {'todo-postgres.yaml', 'todo-config.yaml', 'todo-app.yaml', 'notes-postgres.yaml', 'notes-config.yaml',
                 'notes-app.yaml', 'keycloak-postgres.yaml', 'keycloak-config.yaml', 'keycloak.yaml',
                 'shared-proxy.yaml'}
TODO_MANIFESTS = {'todo-postgres.yaml', 'todo-config.yaml', 'todo-app.yaml', 'keycloak-postgres.yaml',
                  'keycloak-config.yaml', 'keycloak.yaml', 'shared-proxy.yaml'}
SERVING = {'shared-proxy.service', 'todo-app.service', 'notes-app.service', 'keycloak.service'}
DATABASES = {'todo-postgres.service', 'notes-postgres.service', 'keycloak-postgres.service'}


def events(calls):
    """The start, wait, role-setup and play commands among calls, in order."""
    found = []
    for argv in calls:
        if argv[:3] == ['systemctl', '--user', 'start'] and argv[3].endswith('.service'):
            found.append(('start', argv[3].removesuffix('.service')))
        elif argv[:2] == ['podman', 'wait']:
            found.append(('wait', argv[-1]))
        elif argv[-1:] == ['backend.setup_roles']:
            host = next(word for word in argv if word.startswith('DATABASE_HOST='))
            found.append(('roles', host.removeprefix('DATABASE_HOST=').removesuffix('-postgres')))
        elif argv[:3] == ['podman', 'kube', 'play'] and '--help' not in argv and '--down' not in argv:
            config = argv[argv.index('--configmap') + 1] if '--configmap' in argv else None
            found.append(('play', Path(argv[-1]).name, config and Path(config).name))
    return found


class ServerOrderTests(unittest.TestCase):
    def install(self, applications, change_everything=False):
        """Install from a real offline bundle on a fake host; with change_everything, again after every file changed."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'quadlet'
            runtime = directory / settings.KUBE_RUNTIME
            offline_bundle.build(root, applications)
            with FakeHost(unit_directory=runtime) as host, patch.object(keycloak, 'configure'):
                install.install(ROOT, mode='server', deployment_mode='offline', bundle_directory=root,
                                quadlet_dir=directory, applications=applications)
                installed = ({path.name for path in runtime.glob('*.yaml')},
                             {path.name for path in runtime.glob('*.kube')})
                if change_everything:
                    for path in (root / 'generated/target').rglob('*'):
                        if path.is_file():
                            path.write_bytes(path.read_bytes() + b'\n# changed\n')
                    host.calls.clear()
                    install.install(ROOT, mode='server', deployment_mode='offline', bundle_directory=root,
                                    quadlet_dir=directory, applications=applications)
                return list(host.calls), installed

    def test_every_app_starts_in_order_with_its_waits_and_role_setup(self):
        for applications, expected, starts in ((apps.APPS, ALL_SERVER_EVENTS, ALL_STARTS),
                                               (TODO, TODO_SERVER_EVENTS, TODO_STARTS)):
            with self.subTest([app.name for app in applications]):
                calls, (manifests, units) = self.install(applications)
                self.assertEqual(events(calls), expected)
                self.assertEqual(units, {name + '.kube' for name in starts})
                self.assertEqual(manifests, ALL_MANIFESTS if applications == apps.APPS else TODO_MANIFESTS)
                shown = [argv[3] for argv in calls if argv[:3] == ['systemctl', '--user', 'show']]
                self.assertEqual(sorted(shown), sorted(name + '.service' for name in starts))

    def test_a_change_to_everything_stops_every_workload_once_then_starts_in_order(self):
        calls, _installed = self.install(apps.APPS, change_everything=True)
        stops = [argv[3] for argv in calls if argv[:3] == ['systemctl', '--user', 'stop']]
        self.assertEqual(sorted(stops), sorted(name + '.service' for name in ALL_STARTS))
        last_stop = max(index for index, argv in enumerate(calls) if argv[:3] == ['systemctl', '--user', 'stop'])
        first_start = min(index for index, argv in enumerate(calls) if argv[:3] == ['systemctl', '--user', 'start'])
        self.assertLess(last_stop, first_start)
        self.assertEqual(events(calls), ALL_SERVER_EVENTS)

    def test_an_offline_install_needs_exactly_these_files(self):
        manifests, units = install.offline_files(apps.APPS)
        self.assertEqual(manifests, ALL_MANIFESTS)
        self.assertEqual(units, {name + '.kube' for name in ALL_STARTS})
        manifests, units = install.offline_files(TODO)
        self.assertEqual(manifests, TODO_MANIFESTS)
        self.assertEqual(units, {name + '.kube' for name in TODO_STARTS})


class DevelopmentOrderTests(unittest.TestCase):
    def test_every_app_plays_in_order_with_its_waits_and_role_setup(self):
        for applications, expected in ((apps.APPS, ALL_DEV_EVENTS), (TODO, TODO_DEV_EVENTS)):
            with self.subTest([app.name for app in applications]), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                for part in ('deploy/environments', 'deploy/quadlet'):
                    (root / part).parent.mkdir(parents=True, exist_ok=True)
                    (root / part).symlink_to(ROOT / part)
                with RenderingHost() as host, patch.object(keycloak, 'configure'), \
                        patch.object(settings, 'DEV_STATE_FILE', root / 'dev.json'):
                    install.install(root, mode='dev', applications=applications)
                self.assertEqual(events(host.calls), expected)

    def test_tear_down_goes_in_reverse_from_the_record_and_without_it(self):
        for applications, expected in ((apps.APPS, ALL_TEARDOWN), (TODO, TODO_TEARDOWN)):
            with self.subTest([app.name for app in applications]), tempfile.TemporaryDirectory() as temp:
                directory, state = Path(temp) / 'rendered', Path(temp) / 'dev.json'
                directory.mkdir()
                for name in ALL_MANIFESTS:
                    (directory / name).write_text(f'# {name}\n')
                with FakeHost() as host:
                    self.assertTrue(kube_play.up(directory, applications, state))
                    host.calls.clear()
                    kube_play.down(directory, state_file=state)
                recorded = [argv for argv in host.calls if argv[:4] == ['podman', 'kube', 'play', '--down']]
                self.assertEqual(len(recorded), len(expected))
                with patch('app_installer.kube_play.run') as run, patch.object(secrets, 'remove_kube_volumes'):
                    self.assertTrue(kube_play.down(directory, applications, state_file=state))
                fallback = [Path(call.args[-1]).name for call in run.call_args_list]
                self.assertEqual(fallback, expected)
            with tempfile.TemporaryDirectory() as temp, FakeHost():
                # The recorded YAML itself, in the same order.
                directory, state = Path(temp) / 'rendered', Path(temp) / 'dev.json'
                directory.mkdir()
                for name in ALL_MANIFESTS:
                    (directory / name).write_text(f'# {name}\n')
                kube_play.up(directory, applications, state)
                teardown = json.loads(state.read_text())['teardown']
                self.assertEqual([text.removeprefix('# ').strip() for text in teardown], expected)


class StopOrderTests(unittest.TestCase):
    def test_uninstall_removes_the_pods_in_reverse_start_order(self):
        self.assertEqual(uninstall.PODS, ('shared-proxy', 'notes-app', 'todo-app', 'keycloak',
                                          'keycloak-postgres', 'notes-postgres', 'todo-postgres'))

    def test_uninstall_stops_every_current_and_old_service_at_once(self):
        self.assertEqual(set(uninstall.SERVICES), {
            'todo-app', 'todo-frontend', 'todo-backend', 'todo-db-grants', 'todo-migrate', 'todo-db-setup',
            'todo-postgres', 'notes-app', 'notes-frontend', 'notes-backend', 'notes-db-grants',
            'notes-migrate', 'notes-db-setup', 'notes-postgres', 'keycloak', 'keycloak-postgres',
            'shared-proxy', 'app-network-network'})

    def test_services_name_the_serving_tier_before_the_databases(self):
        services = apps.services()
        self.assertEqual(set(services), SERVING | DATABASES)
        self.assertEqual(services[0], 'shared-proxy.service')
        self.assertEqual(set(services[:4]), SERVING)
        self.assertEqual(set(services[4:]), DATABASES)
        self.assertEqual(set(apps.services(databases=False)), SERVING)
        self.assertEqual(apps.services(databases=False)[0], 'shared-proxy.service')
        self.assertEqual(set(apps.services(TODO)), {'shared-proxy.service', 'todo-app.service', 'keycloak.service',
                                                    'todo-postgres.service', 'keycloak-postgres.service'})

    def test_a_restore_stops_everything_at_once_and_starts_in_this_order(self):
        from test_backup import BackupHost, BackupTest
        test = BackupTest()
        test.setUp()
        self.addCleanup(test.doCleanups)
        test.install_units()
        host = BackupHost()
        with host, patch.object(backup.socket, 'gethostname', return_value='this-host'):
            backup.restore('this-host', test.quadlet)
        stops = [argv[3:] for argv in host.calls if argv[:3] == ['systemctl', '--user', 'stop']]
        self.assertEqual(len(stops), 1)
        self.assertEqual(set(stops[0]), SERVING | DATABASES)
        found = events(host.calls)
        self.assertEqual(found[:6], [
            ('start', 'todo-postgres'), ('wait', 'todo-postgres'),
            ('start', 'notes-postgres'), ('wait', 'notes-postgres'),
            ('start', 'keycloak-postgres'), ('wait', 'keycloak-postgres'),
        ])
        # Then every service once more, in one systemctl call each: the databases
        # (already started, so nothing happens), Keycloak before the apps, nginx last.
        again = [name for _kind, name in found[6:]]
        self.assertEqual(sorted(again), sorted(ALL_STARTS))
        self.assertEqual(set(again[:3]), {'todo-postgres', 'notes-postgres', 'keycloak-postgres'})
        self.assertEqual(again[3], 'keycloak')
        self.assertEqual(set(again[4:6]), {'todo-app', 'notes-app'})
        self.assertEqual(again[6], 'shared-proxy')


if __name__ == '__main__':
    unittest.main()
