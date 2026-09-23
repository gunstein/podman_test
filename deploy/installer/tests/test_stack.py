import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from todo_installer.stack import Database  # noqa: E402


class DatabaseTests(unittest.TestCase):
    def test_owns_every_derived_name_like_the_app_it_will_back(self):
        for name in ("todo", "notes", "third"):
            database = Database(name, name)
            self.assertEqual(database.resource("postgres"), name + "-postgres")
            self.assertEqual(database.unit("postgres"), name + "-postgres.kube")
            self.assertEqual(database.service("postgres"), name + "-postgres.service")
            self.assertEqual(database.manifest("postgres"), "postgres.yaml" if name == "todo"
                             else name + "-postgres.yaml")
            self.assertEqual(database.manifest("config"), "config.yaml" if name == "todo"
                             else name + "-config.yaml")
            self.assertEqual(database.secret("db"), name + "-db-password")
            self.assertEqual(database.kube_secret("postgres"), name + "-kube-postgres-secret")
            self.assertEqual(database.database_role("replicator"), name + "_replicator")
            self.assertEqual(database.replication_slot(), name + "_standby")
            self.assertEqual(database.replication_slot(rebuilt=True), name + "_rebuilt_standby")
            self.assertEqual(database.replication_passfile(), "." + name + "-replication.pgpass")
            self.assertEqual(database.volume("data"), name + "-postgres-data")
            self.assertEqual(database.legacy_volume_service("data"), name + "-postgres-data-volume")
            self.assertEqual(database.image("postgres"), "docker.io/library/postgres:17.11")
            self.assertEqual(database.image_archive("postgres"), "postgres-17.11.tar")

    def test_replication_port_defaults_and_is_overridable(self):
        self.assertEqual(Database("todo", "todo").replication_port, 5432)
        self.assertEqual(Database("notes", "notes", 5433).replication_port, 5433)

    def test_frozen(self):
        import dataclasses
        database = Database("todo", "todo")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            database.name = "changed"


if __name__ == "__main__":
    unittest.main()
