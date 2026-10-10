"""platform.yaml and app.yaml: what they may say, and what a mistake in them says back."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, platform_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PLATFORM = {
    'identityHostname': 'login.example.org', 'publicPort': 8443, 'logLevel': 'info',
    'environments': {'local': {'logLevel': 'debug'}, 'prod': {}},
    'apps': [{'path': 'apps/shop', 'hostname': 'shop.example.org', 'replicationPort': 5440}],
}
SHOP = {'name': 'shop', 'keycloakClient': 'shop-frontend',
        'images': {'backend': {'context': '.'}, 'frontend': {'context': '.'}},
        'endpoints': {'site': {'port': 8080}, 'api': {'port': 8000}},
        'routes': [{'path': '/api/', 'to': 'api'}, {'path': '/ready', 'to': 'api', 'exact': True},
                   {'path': '/', 'to': 'site'}]}


class PlatformFileTests(unittest.TestCase):
    def write(self, platform=None, app=None):
        """A platform.yaml and apps/shop/app.yaml in a new directory; return platform.yaml's path."""
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        (directory / 'apps/shop').mkdir(parents=True)
        (directory / 'apps/shop/app.yaml').write_text(yaml.safe_dump(SHOP if app is None else app, sort_keys=False))
        (directory / 'platform.yaml').write_text(yaml.safe_dump(PLATFORM if platform is None else platform))
        return directory / 'platform.yaml'

    def refused(self, message, **files):
        with self.assertRaisesRegex(ValueError, message):
            platform_file.load(self.write(**files))

    def test_the_repository_describes_todo_and_notes(self):
        platform, local = platform_file.load(ROOT / 'platform.yaml', 'local')
        self.assertEqual([app.name for app in platform.apps], ['todo', 'notes'])
        self.assertEqual([(app.hostname, app.keycloak_client, app.replication_port, app.api_path())
                          for app in platform.apps],
                         [('todo.test', 'todo-frontend', 5432, '/api/todos'),
                          ('notes.test', 'notes-frontend', 5433, '/api/notes')])
        self.assertEqual(platform.identity_hostname, 'auth.test')
        self.assertEqual(local, platform_file.Environment(public_port=8443, log_level='debug'))
        self.assertEqual(platform_file.load(ROOT / 'platform.yaml', 'prod')[1].log_level, 'info')
        self.assertEqual(platform_file.checkout(), platform)

    def test_an_app_comes_from_its_directory_relative_to_platform_yaml(self):
        platform, prod = platform_file.load(self.write())
        self.assertEqual(platform, apps.Platform(apps=(apps.App(
            name='shop', hostname='shop.example.org', keycloak_client='shop-frontend', replication_port=5440,
            images=(apps.AppImage(name='backend', context='apps/shop'),
                    apps.AppImage(name='frontend', context='apps/shop')),
            endpoints=(apps.Endpoint(name='site', port=8080), apps.Endpoint(name='api', port=8000)),
            routes=(apps.Route(path='/api/', to='api'), apps.Route(path='/ready', to='api', exact=True),
                    apps.Route(path='/', to='site'))),),
            identity_hostname='login.example.org'))
        self.assertEqual(prod, platform_file.Environment(public_port=8443, log_level='info'))

    def test_unknown_and_missing_fields_are_errors_that_name_the_file(self):
        self.refused(r'platform.yaml: unknown field realm', platform={**PLATFORM, 'realm': 'todo'})
        self.refused(r'platform.yaml: missing identityHostname',
                     platform={key: value for key, value in PLATFORM.items() if key != 'identityHostname'})
        self.refused(r'app.yaml: unknown field database', app={**SHOP, 'database': True})
        self.refused(r'app.yaml: missing keycloakClient', app={'name': 'shop'})
        self.refused(r'apps\[0\]: unknown field name',
                     platform={**PLATFORM, 'apps': [{**PLATFORM['apps'][0], 'name': 'shop'}]})

    def test_an_environment_changes_only_the_port_and_the_log_level(self):
        self.refused(r'environments.local: unknown field identityHostname',
                     platform={**PLATFORM, 'environments': {'local': {'identityHostname': 'x.test'}, 'prod': {}}})
        self.refused(r'environments: missing prod', platform={**PLATFORM, 'environments': {'local': {}}})
        with self.assertRaisesRegex(ValueError, "no environment 'staging'"):
            platform_file.load(self.write(), 'staging')
        platform = {**PLATFORM, 'environments': {'local': {'publicPort': 9443}, 'prod': {}}}
        self.assertEqual(platform_file.load(self.write(platform=platform), 'local')[1].public_port, 9443)

    def test_values_are_checked(self):
        for platform, message in (
            ({**PLATFORM, 'publicPort': 443}, r'publicPort: must be a port number, 1024-65535'),
            ({**PLATFORM, 'publicPort': 70000}, r'publicPort: must be a port number'),
            ({**PLATFORM, 'publicPort': '8443'}, r'publicPort: must be a port number'),
            ({**PLATFORM, 'publicPort': True}, r'publicPort: must be a port number'),
            ({**PLATFORM, 'logLevel': ''}, r'logLevel: must be a word'),
            ({**PLATFORM, 'identityHostname': 'evil.test; return 200'}, r'identityHostname: not a hostname'),
            ({**PLATFORM, 'apps': []}, r'apps must be a list of at least one app'),
            ({**PLATFORM, 'apps': [{**PLATFORM['apps'][0], 'hostname': 'Shop.test'}]}, r'apps\[0\].hostname'),
            ({**PLATFORM, 'apps': [{**PLATFORM['apps'][0], 'replicationPort': '5440'}]}, r'replicationPort'),
            ({**PLATFORM, 'apps': [{**PLATFORM['apps'][0], 'path': 'apps/none'}]}, r'apps/none/app.yaml: cannot be read'),
        ):
            with self.subTest(message=message):
                self.refused(message, platform=platform)
        for app, message in (({**SHOP, 'name': 'my-shop'}, r'app.yaml: name: must be a word matching'),
                             ({**SHOP, 'name': 'identity'}, r'platform.yaml: The app name identity is reserved'),
                             ({**SHOP, 'apiCollection': '/api'}, r'apiCollection')):
            with self.subTest(message=message):
                self.refused(message, app=app)

    def test_two_apps_may_not_share_a_name_client_hostname_or_port(self):
        second = {'path': 'apps/other', 'hostname': 'other.example.org', 'replicationPort': 5441}
        other = {**SHOP, 'name': 'other', 'keycloakClient': 'other-frontend'}
        for change, field in (({'hostname': 'shop.example.org'}, 'hostname'), ({'replicationPort': 5440}, 'replicationPort')):
            with self.subTest(field=field):
                path = self.write(platform={**PLATFORM, 'apps': [PLATFORM['apps'][0], {**second, **change}]})
                (path.parent / 'apps/other').mkdir()
                (path.parent / 'apps/other/app.yaml').write_text(yaml.safe_dump(other))
                with self.assertRaisesRegex(ValueError, rf'the apps apps/shop/app.yaml and apps/other/app.yaml share {field}'):
                    platform_file.load(path)
        for change, field in (({'name': 'shop'}, 'name'), ({'keycloakClient': 'shop-frontend'}, 'keycloakClient')):
            with self.subTest(field=field):
                path = self.write(platform={**PLATFORM, 'apps': [PLATFORM['apps'][0], second]})
                (path.parent / 'apps/other').mkdir()
                (path.parent / 'apps/other/app.yaml').write_text(yaml.safe_dump({**other, **change}))
                with self.assertRaisesRegex(ValueError, rf'share {field}'):
                    platform_file.load(path)

    def test_routes_go_to_the_apps_own_endpoints(self):
        platform, _ = platform_file.load(self.write())
        app = platform.apps[0]
        self.assertEqual([route.location for route in app.routes], ['/api/', '= /ready', '/'])
        self.assertEqual(apps.Platform.from_json(platform.to_json()), platform)
        todo = platform_file.checkout().apps[0]
        self.assertEqual([(route.location, route.to) for route in todo.routes],
                         [('/api/', 'backend'), ('= /health', 'backend'), ('= /ready', 'backend'), ('/', 'frontend')])

    def test_endpoints_and_routes_are_checked(self):
        endpoints = SHOP['endpoints']
        for change, message in (
            ({'endpoints': {}}, r'endpoints must map each endpoint name to its port'),
            ({'endpoints': {'site': {'port': 0}}}, r'endpoints.site.port: must be a port number'),
            ({'endpoints': {'site': {'port': 8080, 'container': 'x'}}}, r'endpoints.site: unknown field container'),
            ({'routes': []}, r'routes must be a list'),
            ({'routes': [{'path': '/', 'to': 'db'}]}, r"routes\[0\].to: 'db' is not one of the endpoints site, api"),
            ({'routes': [{'path': '/api', 'to': 'api'}]}, r'a prefix path ends with a slash \(/api/\)'),
            ({'routes': [{'path': 'api/', 'to': 'api'}]}, r'routes\[0\].path: must be a path'),
            ({'routes': [{'path': '/a b/', 'to': 'api'}]}, r'routes\[0\].path: must be a path'),
            ({'routes': [{'path': '/x;return 200;/', 'to': 'api'}]}, r'routes\[0\].path: must be a path'),
            ({'routes': [{'path': '/auth/', 'to': 'api'}]}, r"/auth/ is the platform's"),
            ({'routes': [{'path': '/auth/admin/', 'to': 'api'}]}, r"is the platform's"),
            ({'routes': [{'path': '/auth', 'to': 'api', 'exact': True}]}, r"is the platform's"),
            ({'routes': [{'path': '/', 'to': 'site'}, {'path': '/', 'to': 'api'}]}, r'routes\[1\]: a second route for /'),
            ({'routes': [{'path': '/ready', 'to': 'api', 'exact': 'yes'}]}, r'exact: must be true or false'),
            ({'routes': [{'path': '/', 'to': 'site', 'rewrite': 'strip'}]}, r'unknown field rewrite'),
        ):
            with self.subTest(message=message):
                self.refused(message, app={**SHOP, 'endpoints': endpoints, **change})
        # An exact path and a prefix path with the same text are two locations.
        app = {**SHOP, 'routes': [{'path': '/ready/', 'to': 'api'}, {'path': '/ready/', 'to': 'api', 'exact': True}]}
        self.assertEqual(len(platform_file.load(self.write(app=app))[0].apps[0].routes), 2)

    def test_an_image_is_built_from_a_context_relative_to_its_app(self):
        app = {**SHOP, 'images': {
            'backend': {'context': '../..', 'containerfile': 'src/backend/Containerfile'},
            'frontend': {'context': '../../../elsewhere/site'},
        }}
        platform, _ = platform_file.load(self.write(app=app))
        self.assertEqual(platform.apps[0].images, (
            apps.AppImage(name='backend', context='.', containerfile='src/backend/Containerfile'),
            apps.AppImage(name='frontend', context='../elsewhere/site', containerfile='Containerfile')))
        self.assertEqual(apps.Platform.from_json(platform.to_json()), platform)

    def test_images_are_checked(self):
        for images, message in (
            (None, r'app.yaml: missing images'),
            ({}, r'images must map each image name'),
            ({'Site': {'context': '.'}}, r'images.Site: must be a word'),
            ({'site': {}}, r'images.site: missing context'),
            ({'site': {'context': '.', 'tag': 'x'}}, r'images.site: unknown field tag'),
            ({'site': {'context': '.', 'containerfile': '../Containerfile'}}, r'must be a path inside the context'),
            ({'site': {'context': '.', 'containerfile': '/etc/Containerfile'}}, r'must be a path inside the context'),
            ({'backend': {'context': '.'}}, r'app.yaml: images: the shared app pod template .* declare frontend'),
        ):
            with self.subTest(message=message):
                app = {key: value for key, value in SHOP.items() if key != 'images'}
                if images is not None:
                    app['images'] = images
                self.refused(message, app=app)

    def test_keycloaks_database_port_is_taken(self):
        self.refused(r"apps/shop/app.yaml has replicationPort 5434, which is Keycloak's database's",
                     platform={**PLATFORM, 'apps': [{**PLATFORM['apps'][0], 'replicationPort': 5434}]})

    def test_an_environment_is_a_mapping_or_empty(self):
        for value in (False, [], 'debug'):
            with self.subTest(value=value):
                self.refused(r'environments.prod: must be a mapping',
                             platform={**PLATFORM, 'environments': {'local': {}, 'prod': value}})
        self.assertEqual(platform_file.load(self.write(
            platform={**PLATFORM, 'environments': {'local': None, 'prod': None}}))[1].log_level, 'info')

    def test_not_yaml_is_an_error_that_names_the_file(self):
        path = self.write()
        path.write_text('apps: [\n')
        with self.assertRaisesRegex(ValueError, r'platform.yaml: not valid YAML'):
            platform_file.load(path)


if __name__ == '__main__':
    unittest.main()
