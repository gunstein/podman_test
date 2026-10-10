"""target_render: only ${TARGET_...} is replaced, every value is checked, and bundle.json paths stay inside."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offline_bundle  # noqa: E402
from app_installer import apps, target_render  # noqa: E402
from app_installer.target_render import (  # noqa: E402
    IDENTITY_HOSTNAME,
    PUBLISH_ADDRESS,
    TargetError,
)

TODO, NOTES = 'TARGET_TODO_HOSTNAME', 'TARGET_NOTES_HOSTNAME'
VALUES = {IDENTITY_HOSTNAME: 'shop.example.org', TODO: 'todo.example.org', NOTES: 'notes.example.org',
          PUBLISH_ADDRESS: '192.0.2.10'}
NAMES = [IDENTITY_HOSTNAME, PUBLISH_ADDRESS]  # Keycloak's hostname and the address, unless a test says otherwise


def resolve(given, environment=None, recorded=None, defaults=None, names=NAMES):
    return target_render.resolve(given, environment or {}, recorded or {}, defaults or {}, names)


class SubstituteTests(unittest.TestCase):
    def test_only_target_placeholders_are_replaced(self):
        text = ('server_name ${TARGET_IDENTITY_HOSTNAME};\n'
                'cd $HOME && psql "password=${DATABASE_PASSWORD}" $$ ${target_lower} $TARGET_IDENTITY_HOSTNAME\n')
        self.assertEqual(target_render.substitute(text, VALUES, 'test'),
                         'server_name shop.example.org;\n'
                         'cd $HOME && psql "password=${DATABASE_PASSWORD}" $$ ${target_lower} $TARGET_IDENTITY_HOSTNAME\n')

    def test_an_unknown_placeholder_or_a_missing_value_is_an_error(self):
        with self.assertRaisesRegex(TargetError, r'f.yaml: no value for \$\{TARGET_HOSTNAME\}'):
            target_render.substitute('${TARGET_HOSTNAME}', VALUES, 'f.yaml')
        with self.assertRaisesRegex(TargetError, r'no value for \$\{TARGET_PUBLISH_ADDRESS\}'):
            target_render.substitute('${TARGET_PUBLISH_ADDRESS}', {IDENTITY_HOSTNAME: 'a.test'}, 'f.kube')


class ResolveTests(unittest.TestCase):
    def test_keycloak_and_every_app_have_their_own_hostname_value(self):
        self.assertEqual(target_render.hostname_targets(apps.registry()), [IDENTITY_HOSTNAME, TODO, NOTES])
        self.assertEqual([target_render.hostname_target(app) for app in apps.registry().apps], [TODO, NOTES])
        self.assertEqual(target_render.hostnames(VALUES, apps.registry()),
                         {'todo': 'todo.example.org', 'notes': 'notes.example.org'})
        self.assertEqual(target_render.identity_hostname(VALUES), 'shop.example.org')

    def test_command_line_then_environment_then_host_record_then_bundle_default(self):
        defaults, recorded = {IDENTITY_HOSTNAME: 'todo.test'}, {IDENTITY_HOSTNAME: 'recorded.example.org'}
        environment = {IDENTITY_HOSTNAME: 'env.example.org', PUBLISH_ADDRESS: '192.0.2.99'}
        both = {IDENTITY_HOSTNAME: 'cli.example.org', PUBLISH_ADDRESS: '192.0.2.10'}
        self.assertEqual(resolve(both, environment, recorded, defaults), both)
        only_address = {IDENTITY_HOSTNAME: None, PUBLISH_ADDRESS: '192.0.2.10'}
        self.assertEqual(resolve(only_address, environment, recorded, defaults)[IDENTITY_HOSTNAME], 'env.example.org')
        self.assertEqual(resolve(only_address, {}, recorded, defaults)[IDENTITY_HOSTNAME], 'recorded.example.org')
        self.assertEqual(resolve(only_address, {}, {}, defaults)[IDENTITY_HOSTNAME], 'todo.test')
        # The publish address never comes from the environment, a record or a default.
        with self.assertRaisesRegex(TargetError, 'TARGET_PUBLISH_ADDRESS has no value'):
            resolve({IDENTITY_HOSTNAME: 'a.test'}, environment, {PUBLISH_ADDRESS: '192.0.2.1'},
                    {PUBLISH_ADDRESS: '192.0.2.2'})

    def test_only_the_bundles_apps_need_a_hostname(self):
        self.assertNotIn(NOTES, resolve({PUBLISH_ADDRESS: '192.0.2.10'}, defaults={IDENTITY_HOSTNAME: 'a.test'}))
        with self.assertRaisesRegex(TargetError, 'TARGET_NOTES_HOSTNAME has no value'):
            resolve({PUBLISH_ADDRESS: '192.0.2.10'}, defaults={IDENTITY_HOSTNAME: 'a.test'},
                    names=[IDENTITY_HOSTNAME, NOTES, PUBLISH_ADDRESS])

    def test_the_machine_hostname_is_never_the_public_hostname(self):
        with self.assertRaisesRegex(TargetError, 'TARGET_IDENTITY_HOSTNAME has no value'):
            resolve({PUBLISH_ADDRESS: '192.0.2.10'}, {'HOSTNAME': 'todo-primary'})

    def test_values_are_checked_where_they_are_used(self):
        names = [IDENTITY_HOSTNAME, NOTES, PUBLISH_ADDRESS]
        for name, value in ((IDENTITY_HOSTNAME, 'shop.example.org; return 200'),
                            (IDENTITY_HOSTNAME, 'Shop.example.org'), (IDENTITY_HOSTNAME, 'shop.test\n'),
                            (IDENTITY_HOSTNAME, '-shop.test'), (IDENTITY_HOSTNAME, '${TARGET_PUBLISH_ADDRESS}'),
                            (NOTES, 'notes.test; return 200'), (NOTES, 'notes..test'),
                            (PUBLISH_ADDRESS, '0.0.0.0'), (PUBLISH_ADDRESS, '224.0.0.1'),
                            (PUBLISH_ADDRESS, 'example.org'), (PUBLISH_ADDRESS, '192.0.2.10 -p 1:1')):
            with self.subTest(name=name, value=value), \
                    self.assertRaisesRegex(TargetError, f'{name} from the command line is not valid'):
                resolve({**VALUES, name: value}, names=names)
        with self.assertRaisesRegex(TargetError, 'from the environment is not valid'):
            resolve({PUBLISH_ADDRESS: '192.0.2.10'}, {IDENTITY_HOSTNAME: 'bad host'})
        with self.assertRaisesRegex(TargetError, 'from the host record is not valid'):
            resolve({PUBLISH_ADDRESS: '192.0.2.10'}, recorded={IDENTITY_HOSTNAME: 'bad host'})


class RecordTests(unittest.TestCase):
    """The host's record holds the hostnames only, never the address."""

    def test_write_then_read_keeps_the_hostnames_and_drops_the_address(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / '.config/platform/target-values.json'
            with patch.object(target_render, 'record_path', return_value=path):
                self.assertEqual(target_render.read_record(), {})
                self.assertTrue(target_render.write_record(VALUES))
                self.assertFalse(target_render.write_record(VALUES))
                self.assertEqual(target_render.read_record(), {IDENTITY_HOSTNAME: 'shop.example.org',
                                                               TODO: 'todo.example.org',
                                                               NOTES: 'notes.example.org'})
                self.assertEqual(oct(path.parent.stat().st_mode & 0o777), '0o700')
                path.write_text(json.dumps({PUBLISH_ADDRESS: '192.0.2.10'}))
                with self.assertRaisesRegex(TargetError, 'may hold only'):
                    target_render.read_record()


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.bundle = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.bundle))
        offline_bundle.build(self.bundle, apps.registry().apps)
        self.metadata = json.loads((self.bundle / 'bundle.json').read_text())

    def write(self, **changes):
        (self.bundle / 'bundle.json').write_text(json.dumps({**self.metadata, **changes}))

    def load(self):
        return target_render.load(self.bundle, VALUES, environment={})

    def test_paths_must_stay_inside_the_bundle(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(outside))
        (outside / 'app-network.network').write_text('[Network]\n')
        os.symlink(outside, self.bundle / 'elsewhere')
        manifests = self.metadata['manifests']
        for changes, message in (
                ({'network': str(outside / 'app-network.network')}, 'must be a path relative to the bundle'),
                ({'network': '../app-network.network'}, 'leaves the bundle'),
                ({'network': 'elsewhere/app-network.network'}, 'leaves the bundle'),
                ({'manifests': {**manifests, 'directory': '/etc'}}, 'must be a path relative to the bundle'),
                ({'manifests': {**manifests, 'directory': 'generated/../..'}}, 'leaves the bundle'),
                ({'manifests': {**manifests, 'files': ['../bundle.json']}}, 'a list of file names'),
                ({'manifests': {**manifests, 'files': manifests['files'] + ['absent.yaml']}}, 'missing')):
            with self.subTest(changes=changes):
                self.write(**changes)
                with self.assertRaisesRegex(TargetError, message):
                    self.load()

    def test_an_older_bundle_is_refused_with_a_clear_reason(self):
        (self.bundle / 'bundle.json').unlink()
        with self.assertRaisesRegex(TargetError, 'no bundle.json: it was built in an older format'):
            self.load()
        self.write(format_version=2)
        with self.assertRaisesRegex(TargetError, 'format version 2; this installer reads version 6'):
            self.load()
        self.write(format='something-else')
        with self.assertRaisesRegex(TargetError, 'does not describe a platform-offline-bundle'):
            self.load()

    def test_units_must_name_the_bundles_own_files(self):
        unit = self.bundle / 'generated/target/quadlet/keycloak.kube'
        unit.write_text(unit.read_text().replace('Network=app-network.network', 'Network=other.network'))
        with self.assertRaisesRegex(TargetError, 'keycloak.kube: Network=other.network is not a file of this bundle'):
            self.load()

    def test_a_placeholder_the_installer_does_not_know_stops_the_load(self):
        manifest = self.bundle / 'generated/target/manifests/keycloak.yaml'
        manifest.write_text(manifest.read_text() + '# ${TARGET_FQDN}\n')
        with self.assertRaisesRegex(TargetError, r'manifests/keycloak.yaml: no value for \$\{TARGET_FQDN\}'):
            self.load()


if __name__ == '__main__':
    unittest.main()
