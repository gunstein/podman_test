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
    EXTERNAL_HOSTNAME,
    PUBLISH_ADDRESS,
    TargetError,
)

VALUES = {EXTERNAL_HOSTNAME: 'shop.example.org', PUBLISH_ADDRESS: '192.0.2.10'}


class SubstituteTests(unittest.TestCase):
    def test_only_target_placeholders_are_replaced(self):
        text = ('server_name ${TARGET_EXTERNAL_HOSTNAME};\n'
                'cd $HOME && psql "password=${DATABASE_PASSWORD}" $$ ${target_lower} $TARGET_EXTERNAL_HOSTNAME\n')
        self.assertEqual(target_render.substitute(text, VALUES, 'test'),
                         'server_name shop.example.org;\n'
                         'cd $HOME && psql "password=${DATABASE_PASSWORD}" $$ ${target_lower} $TARGET_EXTERNAL_HOSTNAME\n')

    def test_an_unknown_placeholder_or_a_missing_value_is_an_error(self):
        with self.assertRaisesRegex(TargetError, r'f.yaml: unknown placeholder \$\{TARGET_HOSTNAME\}'):
            target_render.substitute('${TARGET_HOSTNAME}', VALUES, 'f.yaml')
        with self.assertRaisesRegex(TargetError, r'no value for \$\{TARGET_PUBLISH_ADDRESS\}'):
            target_render.substitute('${TARGET_PUBLISH_ADDRESS}', {EXTERNAL_HOSTNAME: 'a.test'}, 'f.kube')


class ResolveTests(unittest.TestCase):
    def test_command_line_then_environment_then_bundle_default(self):
        defaults = {EXTERNAL_HOSTNAME: 'todo.test'}
        environment = {EXTERNAL_HOSTNAME: 'env.example.org', PUBLISH_ADDRESS: '192.0.2.99'}
        both = {EXTERNAL_HOSTNAME: 'cli.example.org', PUBLISH_ADDRESS: '192.0.2.10'}
        self.assertEqual(target_render.resolve(both, environment, defaults), both)
        only_address = {EXTERNAL_HOSTNAME: None, PUBLISH_ADDRESS: '192.0.2.10'}
        self.assertEqual(target_render.resolve(only_address, environment, defaults)[EXTERNAL_HOSTNAME],
                         'env.example.org')
        self.assertEqual(target_render.resolve(only_address, {}, defaults)[EXTERNAL_HOSTNAME], 'todo.test')
        # The publish address never comes from the environment.
        with self.assertRaisesRegex(TargetError, 'TARGET_PUBLISH_ADDRESS has no value'):
            target_render.resolve({EXTERNAL_HOSTNAME: 'a.test'}, environment, defaults)

    def test_the_machine_hostname_is_never_the_public_hostname(self):
        with self.assertRaisesRegex(TargetError, 'TARGET_EXTERNAL_HOSTNAME has no value'):
            target_render.resolve({PUBLISH_ADDRESS: '192.0.2.10'}, {'HOSTNAME': 'todo-primary'}, {})

    def test_values_are_checked_where_they_are_used(self):
        for name, value in ((EXTERNAL_HOSTNAME, 'shop.example.org; return 200'),
                            (EXTERNAL_HOSTNAME, 'Shop.example.org'), (EXTERNAL_HOSTNAME, 'shop.test\n'),
                            (EXTERNAL_HOSTNAME, '-shop.test'), (EXTERNAL_HOSTNAME, '${TARGET_PUBLISH_ADDRESS}'),
                            (PUBLISH_ADDRESS, '0.0.0.0'), (PUBLISH_ADDRESS, '224.0.0.1'),
                            (PUBLISH_ADDRESS, 'example.org'), (PUBLISH_ADDRESS, '192.0.2.10 -p 1:1')):
            with self.subTest(name=name, value=value), \
                    self.assertRaisesRegex(TargetError, f'{name} from the command line is not valid'):
                target_render.resolve({**VALUES, name: value}, {}, {})
        environment = {EXTERNAL_HOSTNAME: 'bad host'}
        with self.assertRaisesRegex(TargetError, 'from the environment is not valid'):
            target_render.resolve({PUBLISH_ADDRESS: '192.0.2.10'}, environment, {})


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.bundle = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.bundle))
        offline_bundle.build(self.bundle, apps.APPS)
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
        self.write(format_version=1)
        with self.assertRaisesRegex(TargetError, 'format version 1; this installer reads version 2'):
            self.load()
        self.write(format='something-else')
        with self.assertRaisesRegex(TargetError, 'does not describe a todo-offline-bundle'):
            self.load()

    def test_units_must_name_the_bundles_own_files(self):
        unit = self.bundle / 'generated/target/quadlet/keycloak.kube'
        unit.write_text(unit.read_text().replace('Network=app-network.network', 'Network=other.network'))
        with self.assertRaisesRegex(TargetError, 'keycloak.kube: Network=other.network is not a file of this bundle'):
            self.load()

    def test_a_placeholder_the_installer_does_not_know_stops_the_load(self):
        manifest = self.bundle / 'generated/target/manifests/keycloak.yaml'
        manifest.write_text(manifest.read_text() + '# ${TARGET_FQDN}\n')
        with self.assertRaisesRegex(TargetError, r'manifests/keycloak.yaml: unknown placeholder \$\{TARGET_FQDN\}'):
            self.load()


if __name__ == '__main__':
    unittest.main()
