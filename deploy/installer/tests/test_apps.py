import dataclasses
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from todo_installer.apps import (  # noqa: E402
    APPS,
    KEYCLOAK_ADMIN_SECRET,
    KEYCLOAK_DATABASE,
    REPLICATED_DATABASES,
    App,
    describe,
)


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

    def test_app_owns_every_derived_name(self):
        for name in ("todo", "notes", "third"):
            app = App(name, name + ".test", name + "-frontend")
            self.assertEqual(app.resource("postgres"), name + "-postgres")
            self.assertEqual(app.unit("app"), name + "-app.kube")
            self.assertEqual(app.service("app"), name + "-app.service")
            self.assertEqual(app.manifest("app"), "app.yaml" if name == "todo"
                             else name + "-app.yaml")
            self.assertEqual(app.secret("db"), name + "-db-password")
            self.assertEqual(app.kube_secret("postgres"), name + "-kube-postgres-secret")
            self.assertEqual(app.database_role("app"), name + "_app")
            self.assertEqual(app.volume("data"), name + "-postgres-data")
            self.assertEqual(app.legacy_volume_service("data"), name + "-postgres-data-volume")
            self.assertEqual(app.image("backend"), "localhost/" + name + "-backend:m12")
            self.assertEqual(app.image_archive("backend"), name + "-backend-m12.tar")
            self.assertEqual(app.source_directory("backend"), name + "-backend")

    def test_replicated_databases_append_keycloak_last(self):
        self.assertEqual([d.name for d in REPLICATED_DATABASES], ["todo", "notes", "keycloak"])
        self.assertIs(REPLICATED_DATABASES[-1], KEYCLOAK_DATABASE)
        self.assertEqual(KEYCLOAK_DATABASE.replication_port, 5434)

    def test_describe_shape_matches_what_ansible_reads_per_entry(self):
        # replication-apps --details feeds deploy/ansible/tasks/read-replication-registry.yml;
        # every loop there depends on todo/notes describing as applications (hostname,
        # application_unit, manifests.application) and keycloak describing as a bare
        # database (none of those keys), never the other way around.
        for database in REPLICATED_DATABASES:
            entry = describe(database)
            with self.subTest(name=database.name):
                if database.name == "keycloak":
                    for key in ("hostname", "api_path", "application_unit", "keycloak_client"):
                        self.assertNotIn(key, entry)
                    self.assertNotIn("application", entry["manifests"])
                    self.assertEqual(entry["application_service"], "keycloak.service")
                    self.assertIn(KEYCLOAK_ADMIN_SECRET, entry["raw_secrets"])
                else:
                    for key in ("hostname", "api_path", "application_unit", "keycloak_client"):
                        self.assertIn(key, entry)
                    self.assertIn("application", entry["manifests"])
                    self.assertEqual(entry["application_service"], database.name + "-app.service")

    def test_images_come_from_the_app(self):
        from todo_installer.images import image_list, shared_images
        app = App("notes", "notes", "notes.test", "notes-frontend")
        images = image_list(app)
        self.assertEqual([image.reference for image in images], [
            "localhost/notes-backend:m12", "localhost/notes-frontend:m12",
            "docker.io/library/postgres:17.11"])
        self.assertEqual([image.source for image in images], [
            "notes-backend", "notes-frontend", None])
        self.assertEqual(len(shared_images()), 2)

    def test_secret_names_are_isolated_between_apps(self):
        from todo_installer.secrets import application_secret_mapping, postgres_secret_mapping
        mappings = []
        for name in ("todo", "notes"):
            app = App(name, name + ".test", name + "-frontend")
            mapping = {**postgres_secret_mapping(app), **application_secret_mapping(app)}
            self.assertEqual(mapping, {
                name + "-kube-postgres-secret": {"database-password": name + "-db-password"},
                name + "-kube-migrator-secret": {"database-password": name + "-migrator-password"},
                name + "-kube-backend-secret": {"database-password": name + "-app-password"},
            })
            mappings.append(set(mapping) | {v for fields in mapping.values() for v in fields.values()})
        self.assertFalse(mappings[0] & mappings[1])
