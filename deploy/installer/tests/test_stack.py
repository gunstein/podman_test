import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer.stack import Database  # noqa: E402


class DatabaseTests(unittest.TestCase):
    def test_owns_every_derived_name_like_the_app_it_will_back(self):
        for name in ("todo", "notes", "third"):
            database = Database(name)
            self.assertEqual(database.container, name + "-postgres")
            self.assertEqual(database.unit, name + "-postgres.kube")
            self.assertEqual(database.service, name + "-postgres.service")
            self.assertEqual(database.manifest, "todo-postgres.yaml" if name == "todo"
                             else name + "-postgres.yaml")
            self.assertEqual(database.config_manifest, "todo-config.yaml" if name == "todo"
                             else name + "-config.yaml")
            self.assertEqual(database.secret("db"), name + "-db-password")
            self.assertEqual(database.kube_secret, name + "-kube-postgres-secret")
            self.assertEqual(database.role("replicator"), name + "_replicator")
            self.assertEqual(database.replication_slot(), name + "_standby")
            self.assertEqual(database.replication_slot(rebuilt=True), name + "_rebuilt_standby")
            self.assertEqual(database.replication_passfile(), "." + name + "-replication.pgpass")
            self.assertEqual(database.volume("data"), name + "-postgres-data")
            self.assertEqual(database.image, "docker.io/library/postgres:17.11")
            self.assertEqual(database.image_archive, "postgres-17.11.tar")

    def test_replication_port_defaults_and_is_overridable(self):
        self.assertEqual(Database("todo").replication_port, 5432)
        self.assertEqual(Database("notes", 5433).replication_port, 5433)

    def test_frozen(self):
        import dataclasses
        database = Database("todo")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            database.name = "changed"


if __name__ == "__main__":
    unittest.main()
