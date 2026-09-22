import dataclasses
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from todo_installer.apps import APPS, App  # noqa: E402


class AppRegistryTests(unittest.TestCase):
    def test_identities_are_unique_and_safe(self):
        self.assertTrue(APPS)
        for field in ('name', 'hostname', 'keycloak_client'):
            values = [getattr(app, field) for app in APPS]
            self.assertEqual(len(values), len(set(values)), field)
        for app in APPS:
            self.assertRegex(app.name, r'^[a-z][a-z0-9-]*$')
            self.assertRegex(app.chart, r'^[a-z][a-z0-9-]*$')
            self.assertRegex(app.hostname, r'^[a-z0-9.-]+$')
            self.assertTrue(re.fullmatch(r'[a-z0-9-]+', app.keycloak_client))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                app.name = 'changed'

    def test_app_owns_every_derived_name(self):
        for name in ("todo", "notes", "third"):
            app = App(name, name, name + ".test", name + "-frontend")
            self.assertEqual(app.resource("postgres"), name + "-postgres")
            self.assertEqual(app.unit("app"), name + "-app.kube")
            self.assertEqual(app.service("app"), name + "-app.service")
            self.assertEqual(app.manifest("app"), "app.yaml" if name == "todo"
                             else name + "-app.yaml")
            self.assertEqual(app.secret("db"), name + "-db-password")
            self.assertEqual(app.kube_secret("postgres"), name + "-kube-postgres-secret")
            self.assertEqual(app.database_role("app"), name + "_app")
            self.assertEqual(app.volume("data"), name + "-postgres-data")
            self.assertEqual(app.image("backend"), "localhost/" + name + "-backend:m12")
            self.assertEqual(app.image_archive("backend"), name + "-backend-m12.tar")
            self.assertEqual(app.source_directory("backend"), "backend" if name == "todo"
                             else name + "-backend")

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
