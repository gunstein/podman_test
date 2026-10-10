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
    AppImage,
    Platform,
)
from app_installer.platform_file import checkout  # noqa: E402
from app_installer.stack import Database  # noqa: E402

APPS = checkout().apps
REPLICATED_DATABASES = checkout().replicated_databases


class AppRegistryTests(unittest.TestCase):
    def test_identities_are_unique_and_safe(self):
        self.assertTrue(APPS)
        # An app without login has no client, one without a database no
        # replication port; the others must not share theirs.
        for field, apps in (('name', APPS), ('hostname', APPS), ('keycloak_client', checkout().login_apps),
                            ('replication_port', checkout().database_apps)):
            values = [getattr(app, field) for app in apps]
            self.assertEqual(len(values), len(set(values)), field)
        for app in APPS:
            self.assertRegex(app.name, r'^[a-z][a-z0-9-]*$')
            self.assertRegex(app.hostname, r'^[a-z0-9.-]+$')
            if app.keycloak_client:
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
            app = App(name=name, hostname=name + ".test", keycloak_client=name + "-frontend",
                      images=(AppImage(name="backend", context="."),))
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
        self.assertEqual(REPLICATED_DATABASES[:-1], tuple(app.database for app in checkout().database_apps))

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
        app = App(name="notes", hostname="notes.test", keycloak_client="notes-frontend",
                  images=(AppImage(name="backend", context=".", containerfile="notes-backend/Containerfile"),
                          AppImage(name="site", context="../help")))
        images = image_list(app)
        self.assertEqual([image.reference for image in images], [
            "localhost/notes-backend:m12", "localhost/notes-site:m12"])
        self.assertEqual([(image.context, image.containerfile) for image in images],
                         [(".", "notes-backend/Containerfile"), ("../help", "Containerfile")])
        with self.assertRaisesRegex(ValueError, "The app notes declares no image 'frontend'"):
            app.image("frontend")
        # PostgreSQL runs every database, so it is shared, not one per app; and
        # like Keycloak it is there only when some app needs it.
        self.assertEqual([image.reference for image in shared_images(Platform(apps=(app,), identity_hostname="a.test"))],
                         ["docker.io/library/postgres:17.11", "localhost/platform-proxy:m12", "localhost/keycloak:m12"])
        static = App(name="help", hostname="help.test", has_database=False, replication_port=0)
        self.assertEqual([image.reference for image in shared_images(Platform(apps=(static,), identity_hostname="a.test"))],
                         ["localhost/platform-proxy:m12"])

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
    def test_ready_names_what_each_role_runs(self):
        databases = ["todo-postgres", "notes-postgres", "keycloak-postgres"]
        self.assertEqual(checkout().ready("standby"), (databases, databases))
        pods, containers = checkout().ready("app")
        self.assertEqual(pods, [*databases, "keycloak", "todo-app", "notes-app", "help-app", "shared-proxy"])
        self.assertEqual(containers, [*databases, "keycloak", "todo-backend", "todo-frontend",
                                      "notes-backend", "notes-frontend", "help-site", "nginx"])
        with self.assertRaisesRegex(ValueError, "role must be app or standby"):
            checkout().ready("primary")

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
        today, shop = checkout(), self.shop()
        self.assertEqual([app.name for app in today.apps], ["todo", "notes", "help"])
        self.assertEqual([d.name for d in shop.replicated_databases], ["shop", "keycloak"])
        self.assertEqual([d.name for d in today.replicated_databases], ["todo", "notes", "keycloak"])
        self.assertEqual(shop.services(databases=False), ["shared-proxy.service", "shop-app.service",
                                                          "keycloak.service"])
        self.assertIn("todo-app.service", today.services())
        self.assertEqual(checkout(), today)

    def test_the_json_form_gives_the_same_platform_back(self):
        for platform in (checkout(), self.shop(), dataclasses.replace(checkout(), identity_hostname="auth.example.org").select(["notes"])):
            with self.subTest(apps=[app.name for app in platform.apps]):
                data = json.loads(json.dumps(platform.to_json()))
                self.assertEqual(Platform.from_json(data), platform)

    def test_json_that_is_not_a_platform_is_refused(self):
        good = checkout().to_json()
        for data in ([], {}, {**good, "extra": 1}, {**good, "apps": "todo"},
                     {**good, "apps": [{**good["apps"][0], "replication_port": "5432"}]},
                     {**good, "apps": [{**good["apps"][0], "unknown": True}]},
                     {**good, "apps": [good["apps"][0], good["apps"][0]]},
                     {**good, "apps": []}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Platform.from_json(data)

    def test_select_keeps_the_order_and_refuses_unknown_apps(self):
        self.assertEqual(checkout().select([]), checkout())
        self.assertEqual([app.name for app in checkout().select(["notes", "todo"]).apps], ["todo", "notes"])
        with self.assertRaisesRegex(ValueError, "Unknown apps: shop"):
            checkout().select(["shop"])
        with self.assertRaisesRegex(ValueError, "No app named 'shop'"):
            checkout().app("shop")

    def test_postgres_and_keycloak_run_only_when_an_app_needs_them(self):
        from app_installer import install, secrets, workloads
        shop = self.shop().apps[0]
        help_ = App(name="help", hostname="help.example.org", has_database=False, replication_port=0)
        wiki = App(name="wiki", hostname="wiki.example.org", replication_port=5441,
                   containers=("wiki-backend", "wiki-frontend"))
        self.assertEqual((help_.has_database, help_.has_login, wiki.has_database, wiki.has_login),
                         (False, False, True, False))
        with self.assertRaisesRegex(ValueError, "The app help has no database"):
            _ = help_.database

        mixed = Platform(apps=(shop, help_), identity_hostname="login.example.org")
        self.assertEqual([d.name for d in mixed.replicated_databases], ["shop", "keycloak"])
        self.assertEqual(mixed.ready("app")[0],
                         ["shop-postgres", "keycloak-postgres", "keycloak", "shop-app", "help-app", "shared-proxy"])

        # Without login Keycloak and its database are not there, nor its secrets, client or unit dependency.
        data_only = Platform(apps=(wiki,), identity_hostname="login.example.org")
        self.assertFalse(data_only.has_identity)
        self.assertEqual([d.name for d in data_only.replicated_databases], ["wiki"])
        self.assertEqual(data_only.ready("app")[1], ["wiki-postgres", "wiki-backend", "wiki-frontend", "nginx"])
        self.assertEqual(sorted(secrets.installed_names(data_only)),
                         ["wiki-app-password", "wiki-db-password", "wiki-migrator-password"])
        self.assertEqual(install.clients(data_only, {"wiki": "wiki.example.org"}), [])
        self.assertFalse(install.configure_identity(data_only, {"wiki": "wiki.example.org"}))

        # Without a database PostgreSQL is not there either: an app pod and nginx.
        static = Platform(apps=(help_,), identity_hostname="login.example.org")
        self.assertEqual(static.replicated_databases, ())
        self.assertEqual(static.services(), ["shared-proxy.service", "help-app.service"])
        self.assertEqual(static.ready("standby"), ([], []))
        self.assertEqual(static.host_ports(), {"nginx": (8080, 8443)})
        self.assertEqual((secrets.kube_mappings(static), secrets.installed_names(static)), ({}, []))
        self.assertEqual(workloads.proxy_variables("127.0.0.1", 8443, static)["workload"].requires,
                         ("help-app.service",))
        self.assertEqual(Platform.from_json(json.loads(json.dumps(static.to_json()))), static)

    def test_apps_without_login_or_a_database_need_no_client_or_port_of_their_own(self):
        apps = tuple(App(name=name, hostname=f"{name}.test", has_database=False, replication_port=0)
                     for name in ("help", "docs"))
        self.assertEqual(Platform(apps=apps, identity_hostname="auth.test").login_apps, ())

    def test_keycloaks_replication_port_is_not_an_apps(self):
        with self.assertRaisesRegex(ValueError, "Keycloak's database"):
            Platform(apps=(App(name="shop", hostname="shop.test", keycloak_client="shop-frontend",
                               replication_port=KEYCLOAK_DATABASE.replication_port),),
                     identity_hostname="auth.test")
