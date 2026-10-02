"""The offline install from pre-rendered files: real rendering, substitution and file writing.

Only external programs (podman, systemctl) are faked, by FakeHost; the
bundle is rendered from this repository, the target values are filled in
and the files are written into temporary directories for real. The last
test runs the whole offline install in a new Python process where Jinja2
and PyYAML cannot be imported, as on a target host without them.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
import offline_bundle  # noqa: E402
from app_installer import apps, install, keycloak, quadlet, render, workloads  # noqa: E402
from app_installer.target_render import EXTERNAL_HOSTNAME, TargetError  # noqa: E402
from fake_host import FakeHost  # noqa: E402

ROOT = TESTS.parents[2]
HOSTNAME = 'shop.example.org'
ADDRESS = '192.0.2.10'
NOTES_HOSTNAME = 'TARGET_NOTES_HOSTNAME'


def dollar_expressions(text):
    """Every $NAME and ${NAME} in text except the target placeholders."""
    return [match for match in re.findall(r'\$\{?[A-Za-z_][A-Za-z0-9_]*\}?', text)
            if not match.startswith('${TARGET_')]


class BundleContentTests(unittest.TestCase):
    """What build-bundle.sh puts into a bundle for the target host (app_installer.bundle)."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = Path(tempfile.mkdtemp())
        cls.metadata = offline_bundle.build(cls.bundle, apps.APPS)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['rm', '-rf', str(cls.bundle)], check=True)

    def test_metadata_names_every_rendered_file(self):
        data = json.loads((self.bundle / 'bundle.json').read_text())
        self.assertEqual(data, self.metadata)
        self.assertEqual((data['format'], data['format_version']), ('todo-offline-bundle', 3))
        self.assertEqual(data['applications'], ['todo', 'notes'])
        self.assertEqual(data['defaults'], {EXTERNAL_HOSTNAME: 'todo.test', 'TARGET_NOTES_HOSTNAME': 'notes.test'})
        manifests, units = install.offline_files(apps.APPS)
        self.assertEqual(set(data['manifests']['files']), manifests)
        self.assertEqual(set(data['quadlets']['files']), units)
        self.assertEqual(data['local_only_quadlets']['files'], ['shared-proxy.kube'])
        self.assertEqual(set(data['replicated_quadlets']['files']), {d.unit for d in apps.REPLICATED_DATABASES})
        for key in ('manifests', 'quadlets', 'local_only_quadlets', 'replicated_quadlets'):
            for name in data[key]['files']:
                self.assertTrue((self.bundle / data[key]['directory'] / name).is_file(), name)
        self.assertEqual((self.bundle / data['network']).read_bytes(),
                         (ROOT / 'deploy/quadlet/app-network.network').read_bytes())

    def test_every_public_hostname_is_a_placeholder_wherever_it_is_used(self):
        manifests = self.bundle / 'generated/target/manifests'
        proxy = (manifests / 'shared-proxy.yaml').read_text()
        for line in ('TODO_TLS_HOSTNAME: "${TARGET_EXTERNAL_HOSTNAME}"',
                     'APP_TLS_HOSTNAMES: "${TARGET_EXTERNAL_HOSTNAME} ${TARGET_NOTES_HOSTNAME}"',
                     'server_name ${TARGET_EXTERNAL_HOSTNAME};', 'server_name ${TARGET_NOTES_HOSTNAME};',
                     "connect-src 'self' https://${TARGET_EXTERNAL_HOSTNAME}:8443;"):
            self.assertIn(line, proxy)
        for name in ('config.yaml', 'notes-config.yaml'):
            self.assertIn('OIDC_ISSUER: "https://${TARGET_EXTERNAL_HOSTNAME}:8443/auth/realms/todo"',
                          (manifests / name).read_text())
        self.assertIn('KC_HOSTNAME: "https://${TARGET_EXTERNAL_HOSTNAME}:8443/auth"',
                      (manifests / 'keycloak.yaml').read_text())
        everything = ''.join(path.read_text() for path in manifests.iterdir())
        self.assertNotIn('todo.test', everything)
        self.assertNotIn('notes.test', everything)

    def test_the_target_files_are_the_normal_render_with_the_values_in_place(self):
        values = ROOT / 'deploy/environments/prod/values.yaml'
        hostname, port, log_level = render.read_values(values)
        manifests = self.bundle / 'generated/target/manifests'
        normal = render.files(ROOT, apps.APPS, render.hostnames(apps.APPS, hostname), port, log_level)
        self.assertEqual({name: (manifests / name).read_text().replace('${TARGET_EXTERNAL_HOSTNAME}', hostname)
                          .replace('${TARGET_NOTES_HOSTNAME}', 'notes.test').encode() for name in normal}, normal)
        units = self.bundle / 'generated/target/quadlet'
        for database in apps.REPLICATED_DATABASES:
            self.assertEqual((units / database.unit).read_bytes(),
                             quadlet.render(ROOT, database.unit, workloads.postgres_variables(database)))
        for database in apps.REPLICATED_DATABASES:
            replicated = (units / 'replicated' / database.unit).read_text().replace('${TARGET_PUBLISH_ADDRESS}', ADDRESS)
            self.assertEqual(replicated.encode(), quadlet.render(
                ROOT, database.unit, workloads.postgres_variables(database, ADDRESS)))
        published = (units / 'shared-proxy.kube').read_text().replace('${TARGET_PUBLISH_ADDRESS}', ADDRESS)
        self.assertEqual(published.encode(), quadlet.render(
            ROOT, 'shared-proxy.kube', workloads.proxy_variables(ADDRESS, port, apps.APPS)))
        self.assertEqual((units / 'local-only/shared-proxy.kube').read_bytes(), quadlet.render(
            ROOT, 'shared-proxy.kube', workloads.proxy_variables('127.0.0.1', port, apps.APPS)))
        for path in units.glob('*.kube'):
            text = path.read_text()
            self.assertEqual(re.findall(r'^Network=(.*)$', text, re.M), ['app-network.network'], path.name)
            for name in re.findall(r'^(?:Yaml|ConfigMap)=(.*)$', text, re.M):
                self.assertTrue((manifests / name).is_file(), f'{path.name}: {name}')


class OfflineInstallTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ['rm', '-rf', str(self.root)])
        self.bundle, self.quadlet = self.root / 'bundle', self.root / 'quadlet'
        self.runtime = self.quadlet / 'todo-kube-runtime'
        offline_bundle.build(self.bundle, apps.APPS)

    def install(self, host, hostname=HOSTNAME, address=ADDRESS, notes=None):
        with patch.object(keycloak, 'configure', return_value=False) as configure:
            changed = install.install(self.bundle, mode='server', deployment_mode='offline',
                                      bundle_directory=self.bundle, publish_address=address,
                                      quadlet_dir=self.quadlet,
                                      target_values={EXTERNAL_HOSTNAME: hostname, NOTES_HOSTNAME: notes})
        return changed, configure

    def installed(self):
        return {path.name: path.read_text() for path in self.runtime.iterdir()}

    def test_the_target_values_reach_proxy_tls_oidc_and_keycloak(self):
        # The templates have few dollar expressions of their own; add the kinds a
        # Kube file may hold, so the test shows that they are installed untouched.
        app = self.bundle / 'generated/target/manifests/app.yaml'
        app.write_text(app.read_text() + '# cd $HOME; psql "password=${DATABASE_PASSWORD}" $$ ${target_lower}\n')
        with FakeHost(unit_directory=self.runtime) as host:
            changed, configure = self.install(host)
        self.assertTrue(changed)
        files = self.installed()
        self.assertFalse([name for name, text in files.items() if '${TARGET_' in text])
        proxy = files['shared-proxy.yaml']
        self.assertIn(f'TODO_TLS_HOSTNAME: "{HOSTNAME}"', proxy)
        self.assertIn(f'APP_TLS_HOSTNAMES: "{HOSTNAME} notes.test"', proxy)
        self.assertIn(f'server_name {HOSTNAME};', proxy)
        self.assertIn(f'OIDC_ISSUER: "https://{HOSTNAME}:8443/auth/realms/todo"', files['config.yaml'])
        self.assertIn(f'OIDC_ISSUER: "https://{HOSTNAME}:8443/auth/realms/todo"', files['notes-config.yaml'])
        self.assertIn(f'KC_HOSTNAME: "https://{HOSTNAME}:8443/auth"', files['keycloak.yaml'])
        self.assertIn(f'PublishPort={ADDRESS}:8443:8443', files['shared-proxy.kube'])
        configure.assert_called_once_with('fixture-password', [
            ('todo-frontend', HOSTNAME), ('notes-frontend', 'notes.test')])
        # Every other dollar expression is installed exactly as the bundle has it.
        bundle_manifests = self.bundle / 'generated/target/manifests'
        for name in ('app.yaml', 'notes-app.yaml', 'postgres.yaml', 'keycloak.yaml'):
            expressions = dollar_expressions((bundle_manifests / name).read_text())
            self.assertEqual(dollar_expressions(files[name]), expressions, name)
        self.assertIn('# cd $HOME; psql "password=${DATABASE_PASSWORD}" $$ ${target_lower}\n', files['app.yaml'])
        self.assertEqual(oct(self.runtime.stat().st_mode & 0o777), '0o700')
        self.assertEqual(oct((self.runtime / 'shared-proxy.yaml').stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct((self.runtime / 'shared-proxy.kube').stat().st_mode & 0o777), '0o644')
        self.assertTrue((self.quadlet / 'app-network.network').is_file())

    def test_a_host_published_only_on_loopback_gets_the_local_only_proxy_unit(self):
        with FakeHost(unit_directory=self.runtime) as host:
            self.install(host, address='127.0.0.1')
        unit = (self.runtime / 'shared-proxy.kube').read_text()
        self.assertEqual(re.findall(r'^PublishPort=(.*)$', unit, re.M), ['127.0.0.1:8080:8080', '127.0.0.1:8443:8443'])

    def test_bad_or_missing_values_stop_before_anything_changes(self):
        metadata = json.loads((self.bundle / 'bundle.json').read_text())
        for hostname, address, defaults, message in (
                ('shop.example.org;', ADDRESS, metadata['defaults'], 'TARGET_EXTERNAL_HOSTNAME from the command line'),
                (None, ADDRESS, {}, 'TARGET_EXTERNAL_HOSTNAME has no value'),
                (HOSTNAME, '0.0.0.0', metadata['defaults'], 'TARGET_PUBLISH_ADDRESS from the command line')):
            (self.bundle / 'bundle.json').write_text(json.dumps({**metadata, 'defaults': defaults}))
            with self.subTest(message=message), FakeHost(unit_directory=self.runtime) as host, \
                    patch.dict('os.environ', {}, clear=False) as environment:
                environment.pop(EXTERNAL_HOSTNAME, None)
                with self.assertRaisesRegex(TargetError, message):
                    self.install(host, hostname, address)
                self.assertFalse(self.quadlet.exists())
                self.assertEqual(host.calls, [])

    def test_an_invalid_metadata_path_stops_before_anything_changes(self):
        metadata = json.loads((self.bundle / 'bundle.json').read_text())
        (self.bundle / 'bundle.json').write_text(json.dumps({**metadata, 'network': '../../etc/passwd'}))
        with FakeHost(unit_directory=self.runtime) as host, self.assertRaisesRegex(TargetError, 'leaves the bundle'):
            self.install(host)
        self.assertEqual(host.calls, [])
        self.assertFalse(self.quadlet.exists())

    def test_the_same_values_again_change_nothing(self):
        with FakeHost(unit_directory=self.runtime) as host:
            self.install(host)
            before = {path: path.stat().st_mtime_ns for path in self.runtime.iterdir()}
            host.calls.clear()
            changed, _ = self.install(host)
        self.assertFalse(changed)
        self.assertFalse(host.ran('systemctl', '--user', 'stop'))
        self.assertEqual({path: path.stat().st_mtime_ns for path in self.runtime.iterdir()}, before)

    def test_a_new_public_hostname_restarts_the_services_whose_files_it_changes(self):
        with FakeHost(unit_directory=self.runtime) as host:
            self.install(host)
            host.calls.clear()
            changed, configure = self.install(host, hostname='www.example.org')
        self.assertTrue(changed)
        stopped = {argv[3] for argv in host.ran('systemctl', '--user', 'stop')}
        # The hostname is in config.yaml (todo's database, app and proxy), notes-config.yaml,
        # keycloak.yaml and shared-proxy.yaml; keycloak-postgres's files do not have it.
        self.assertEqual(stopped, {'todo-postgres.service', 'todo-app.service', 'notes-postgres.service',
                                   'notes-app.service', 'keycloak.service', 'shared-proxy.service'})
        self.assertIn('server_name www.example.org;', self.installed()['shared-proxy.yaml'])
        configure.assert_called_once_with('fixture-password', [
            ('todo-frontend', 'www.example.org'), ('notes-frontend', 'notes.test')])

    def test_each_app_gets_its_own_public_hostname(self):
        with FakeHost(unit_directory=self.runtime) as host:
            _changed, configure = self.install(host, notes='notes.example.org')
        files = self.installed()
        self.assertIn('server_name notes.example.org;', files['shared-proxy.yaml'])
        self.assertIn(f'APP_TLS_HOSTNAMES: "{HOSTNAME} notes.example.org"', files['shared-proxy.yaml'])
        configure.assert_called_once_with('fixture-password', [
            ('todo-frontend', HOSTNAME), ('notes-frontend', 'notes.example.org')])

    def test_the_host_records_its_hostnames_and_a_later_install_keeps_them(self):
        with FakeHost(unit_directory=self.runtime) as host, patch.dict('os.environ', {}, clear=False) as environment:
            for name in (EXTERNAL_HOSTNAME, NOTES_HOSTNAME):
                environment.pop(name, None)
            self.install(host, notes='notes.example.org')
            self.assertEqual(json.loads(host.record.read_text()),
                             {EXTERNAL_HOSTNAME: HOSTNAME, NOTES_HOSTNAME: 'notes.example.org'})
            self.assertEqual(oct(host.record.stat().st_mode & 0o777), '0o644')
            # An update without the options keeps the names instead of going back to the bundle's defaults.
            host.calls.clear()
            changed, configure = self.install(host, hostname=None)
            self.assertFalse(changed)
            self.assertIn(f'server_name {HOSTNAME};', self.installed()['shared-proxy.yaml'])
            configure.assert_called_once_with('fixture-password', [
                ('todo-frontend', HOSTNAME), ('notes-frontend', 'notes.example.org')])
            # The record never holds the address: it is the host's own, given each time.
            self.assertNotIn('TARGET_PUBLISH_ADDRESS', host.record.read_text())

    def test_a_refused_install_records_nothing(self):
        with FakeHost(unit_directory=self.runtime) as host:
            with self.assertRaises(TargetError):
                self.install(host, hostname='bad host')
            self.assertFalse(host.record.exists())

    def test_an_offline_bundle_installs_in_server_mode_only_and_for_its_own_apps(self):
        with FakeHost(unit_directory=self.runtime) as host:
            with self.assertRaisesRegex(ValueError, 'server mode only'):
                install.install(self.bundle, mode='dev', deployment_mode='offline', bundle_directory=self.bundle,
                                quadlet_dir=self.quadlet)
            with self.assertRaisesRegex(ValueError, 'built for todo, notes'):
                install.install(self.bundle, deployment_mode='offline', bundle_directory=self.bundle,
                                quadlet_dir=self.quadlet, applications=(apps.SHARED_RESOURCE_OWNER,))
            with self.assertRaisesRegex(ValueError, 'built for HTTPS port 8443, not 9443'):
                install.install(self.bundle, deployment_mode='offline', bundle_directory=self.bundle,
                                quadlet_dir=self.quadlet, service_port=9443)
        self.assertEqual(host.calls, [])


# Run in a new Python process: Jinja2 and PyYAML cannot be imported there, the
# installer's CLI runs the offline install as install.sh starts it, and only
# podman and systemctl are faked (FakeHost) and Keycloak's HTTP API.
WITHOUT_JINJA2_AND_PYYAML = r'''
import json, sys

class Blocked:
    """Refuses Jinja2 and PyYAML, as on a target host that does not have them."""
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('jinja2', 'yaml', 'markupsafe'):
            raise ImportError(f'{name} is not installed on this host')
        return None

sys.meta_path.insert(0, Blocked())
installer, tests, bundle, quadlet = sys.argv[1:5]
sys.path[:0] = [installer, tests]
from unittest.mock import patch
from app_installer import cli, keycloak
from fake_host import FakeHost

with FakeHost(unit_directory=__import__('pathlib').Path(quadlet) / 'todo-kube-runtime') as host, \
        patch.object(keycloak, 'configure', return_value=False):
    code = cli.main(['install', '--mode', 'server', '--deployment-mode', 'offline',
                     '--project-root', bundle, '--bundle-dir', bundle, '--publish-address', '192.0.2.10',
                     '--quadlet-dir', quadlet, '--target-external-hostname', 'shop.example.org',
                     '--target-notes-hostname', 'notes.example.org'])
loaded = sorted(name for name in sys.modules if name.split('.')[0] in ('jinja2', 'yaml'))
print(json.dumps({'code': code, 'loaded': loaded, 'started': [a[3] for a in host.ran('systemctl', '--user', 'start')]}))
'''


class WithoutJinjaTests(unittest.TestCase):
    def test_a_new_python_process_without_jinja2_or_pyyaml_runs_the_whole_offline_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, quadlet = root / 'bundle', root / 'quadlet'
            offline_bundle.build(bundle, apps.APPS)
            result = subprocess.run(
                [sys.executable, '-c', WITHOUT_JINJA2_AND_PYYAML, str(TESTS.parent), str(TESTS), str(bundle),
                 str(quadlet)],
                capture_output=True, text=True, env={'PATH': '/usr/bin:/bin', 'HOME': temp,
                                                    'PYTHONDONTWRITEBYTECODE': '1'})
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.strip().splitlines()
            self.assertEqual(json.loads(lines[0]), {'changed': True})
            outcome = json.loads(lines[-1])
            self.assertEqual(outcome['code'], 0, result.stderr)
            self.assertEqual(outcome['loaded'], [])
            self.assertEqual(outcome['started'][-1], 'shared-proxy.service')
            runtime = quadlet / 'todo-kube-runtime'
            manifests, units = install.offline_files(apps.APPS)
            self.assertEqual({path.name for path in runtime.iterdir()}, manifests | units)
            self.assertIn('server_name shop.example.org;', (runtime / 'shared-proxy.yaml').read_text())
            self.assertIn('server_name notes.example.org;', (runtime / 'shared-proxy.yaml').read_text())
            self.assertIn('PublishPort=192.0.2.10:8443:8443', (runtime / 'shared-proxy.kube').read_text())
            self.assertFalse([path.name for path in runtime.iterdir() if '${TARGET_' in path.read_text()])


if __name__ == '__main__':
    unittest.main()
