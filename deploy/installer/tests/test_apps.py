import ast
import dataclasses
import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer.apps import (  # noqa: E402
    KEYCLOAK_DATABASE,
    App,
    Platform,
    registry,
)
from app_installer.stack import Database  # noqa: E402

APPS = registry().apps
REPLICATED_DATABASES = registry().replicated_databases


class AppRegistryTests(unittest.TestCase):
    def test_identities_are_unique_and_safe(self):
        self.assertTrue(APPS)
        for field in ('name', 'hostname', 'keycloak_client', 'replication_port'):
            values = [getattr(app, field) for app in APPS]
            self.assertEqual(len(values), len(set(values)), field)
        for app in APPS:
            self.assertRegex(app.name, r'^[a-z][a-z0-9-]*$')
            self.assertRegex(app.hostname, r'^[a-z0-9.-]+$')
            self.assertTrue(re.fullmatch(r'[a-z0-9-]+', app.keycloak_client))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                app.name = 'changed'

    def test_every_app_is_built_with_keyword_arguments(self):
        # App has several string fields in a row, so App("notes", "notes.test",
        # ...) can put a value in the wrong field and still run. The hosts'
        # Python 3.9 has no dataclass kw_only, so this test enforces it.
        repository = Path(__file__).resolve().parents[3]
        positional = []
        for path in repository.glob("**/*.py"):
            if ".git" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(), str(path))):
                if isinstance(node, ast.Call) and node.args and (
                        getattr(node.func, "id", None) == "App" or getattr(node.func, "attr", None) == "App"):
                    positional.append(f"{path.relative_to(repository)}:{node.lineno}")
        self.assertEqual(positional, [])

    def test_app_owns_every_derived_name(self):
        for name in ("todo", "notes", "third"):
            app = App(name=name, hostname=name + ".test", keycloak_client=name + "-frontend")
            self.assertEqual(app.database.container, name + "-postgres")
            self.assertEqual(app.unit, name + "-app.kube")
            self.assertEqual(app.service, name + "-app.service")
            self.assertEqual(app.manifest, "todo-app.yaml" if name == "todo"
                             else name + "-app.yaml")
            self.assertEqual(app.database.secret("db"), name + "-db-password")
            self.assertEqual(app.database.kube_secret, name + "-kube-postgres-secret")
            self.assertEqual(app.database.role("app"), name + "_app")
            self.assertEqual(app.database.volume("data"), name + "-postgres-data")
            self.assertEqual(app.image("backend"), "localhost/" + name + "-backend:m12")
            self.assertEqual(app.image_archive("backend"), name + "-backend-m12.tar")

    def test_replicated_databases_append_keycloak_last(self):
        self.assertEqual([d.name for d in REPLICATED_DATABASES], ["todo", "notes", "keycloak"])
        self.assertIs(REPLICATED_DATABASES[-1], KEYCLOAK_DATABASE)
        self.assertEqual(KEYCLOAK_DATABASE.replication_port, 5434)

    def test_the_replicated_group_holds_only_databases(self):
        # An App is not a database: DR code takes these, and must not find
        # a hostname or an OAuth client on them.
        for database in REPLICATED_DATABASES:
            self.assertIs(type(database), Database)
        self.assertEqual(REPLICATED_DATABASES[:-1], tuple(app.database for app in APPS))

    def test_replication_apps_details_match_the_acceptance_table(self):
        from app_installer import cli
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["replication-apps", "--details"]), 0)
        self.assertEqual(json.loads(output.getvalue())[1], {
            "name": "notes", "container": "notes-postgres", "service": "notes-postgres.service",
            "replication_port": 5433, "standby_slot": "notes_standby",
            "rebuilt_slot": "notes_rebuilt_standby"})

    def test_images_come_from_the_app(self):
        from app_installer.images import image_list, shared_images
        app = App(name="notes", hostname="notes.test", keycloak_client="notes-frontend")
        images = image_list(app)
        self.assertEqual([image.reference for image in images], [
            "localhost/notes-backend:m12", "localhost/notes-frontend:m12"])
        self.assertEqual([image.source for image in images], ["notes-backend", "notes-frontend"])
        # PostgreSQL runs every database, so it is shared, not one per app.
        self.assertEqual([image.reference for image in shared_images()], [
            "docker.io/library/postgres:17.11", "localhost/platform-proxy:m12", "localhost/keycloak:m12"])

    def test_secret_names_are_isolated_between_apps(self):
        from app_installer.secrets import application_secret_mapping, postgres_secret_mapping
        mappings = []
        for name in ("todo", "notes"):
            app = App(name=name, hostname=name + ".test", keycloak_client=name + "-frontend")
            mapping = {**postgres_secret_mapping(app.database), **application_secret_mapping(app)}
            self.assertEqual(mapping, {
                name + "-kube-postgres-secret": {"database-password": name + "-db-password"},
                name + "-kube-migrator-secret": {"database-password": name + "-migrator-password"},
                name + "-kube-backend-secret": {"database-password": name + "-app-password"},
            })
            mappings.append(set(mapping) | {v for fields in mapping.values() for v in fields.values()})
        self.assertFalse(mappings[0] & mappings[1])



class PlatformTests(unittest.TestCase):
    def shop(self):
        return Platform(apps=(App(name="shop", hostname="shop.example.org", keycloak_client="shop-frontend"),),
                        identity_hostname="login.example.org")

    def test_identity_is_reserved_for_keycloak(self):
        app = App(name="identity", hostname="identity-app.test", keycloak_client="identity-frontend")
        with self.assertRaisesRegex(ValueError, "identity is reserved"):
            Platform(apps=(app,), identity_hostname="auth.test")
        data = self.shop().to_json()
        data["apps"][0]["name"] = "identity"
        with self.assertRaisesRegex(ValueError, "identity is reserved"):
            Platform.from_json(data)

    def test_two_platforms_in_one_process_share_nothing(self):
        today, shop = registry(), self.shop()
        self.assertEqual([app.name for app in today.apps], ["todo", "notes"])
        self.assertEqual([d.name for d in shop.replicated_databases], ["shop", "keycloak"])
        self.assertEqual([d.name for d in today.replicated_databases], ["todo", "notes", "keycloak"])
        self.assertEqual(shop.services(databases=False), ["shared-proxy.service", "shop-app.service",
                                                          "keycloak.service"])
        self.assertIn("todo-app.service", today.services())
        self.assertEqual(registry(), today)

    def test_the_json_form_gives_the_same_platform_back(self):
        for platform in (registry(), self.shop(), registry("auth.example.org").select(["notes"])):
            with self.subTest(apps=[app.name for app in platform.apps]):
                data = json.loads(json.dumps(platform.to_json()))
                self.assertEqual(Platform.from_json(data), platform)

    def test_json_that_is_not_a_platform_is_refused(self):
        good = registry().to_json()
        for data in ([], {}, {**good, "extra": 1}, {**good, "apps": "todo"},
                     {**good, "apps": [{**good["apps"][0], "replication_port": "5432"}]},
                     {**good, "apps": [{**good["apps"][0], "unknown": True}]},
                     {**good, "apps": [good["apps"][0], good["apps"][0]]},
                     {**good, "apps": []}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Platform.from_json(data)

    def test_select_keeps_the_order_and_refuses_unknown_apps(self):
        self.assertEqual(registry().select([]), registry())
        self.assertEqual([app.name for app in registry().select(["notes", "todo"]).apps], ["todo", "notes"])
        with self.assertRaisesRegex(ValueError, "Unknown apps: shop"):
            registry().select(["shop"])
        with self.assertRaisesRegex(ValueError, "No app named 'shop'"):
            registry().app("shop")

    def test_keycloaks_replication_port_is_not_an_apps(self):
        with self.assertRaisesRegex(ValueError, "Keycloak's database"):
            Platform(apps=(App(name="shop", hostname="shop.test", keycloak_client="shop-frontend",
                               replication_port=KEYCLOAK_DATABASE.replication_port),),
                     identity_hostname="auth.test")
