import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "kube" / "runtime"
MIGRATION = ROOT / "ansible" / "roles" / "kube_postgres_primary_migration"
ROLLBACK = ROOT / "ansible" / "roles" / "kube_postgres_primary_rollback"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class KubePostgresMigrationTests(unittest.TestCase):
    def test_manifest_preserves_names_and_volumes(self):
        documents = list(yaml.safe_load_all(read(RUNTIME / "postgres.yaml")))
        claims = {
            document["metadata"]["name"]
            for document in documents
            if document["kind"] == "PersistentVolumeClaim"
        }
        pod = next(document for document in documents if document["kind"] == "Pod")
        container = pod["spec"]["containers"][0]

        self.assertEqual(claims, {"todo-postgres-data", "todo-postgres-backup"})
        self.assertEqual(pod["metadata"]["name"], "todo-postgres")
        self.assertEqual(container["name"], "todo-postgres")
        self.assertEqual(pod["spec"]["restartPolicy"], "Never")
        self.assertEqual(container["securityContext"]["runAsUser"], 999)
        self.assertEqual(container["securityContext"]["runAsGroup"], 999)

    def test_manifest_uses_external_secret_and_no_plaintext(self):
        manifest = read(RUNTIME / "postgres.yaml")

        self.assertIn("secretName: todo-kube-postgres-secret", manifest)
        self.assertIn("/run/secrets/todo-postgres/database-password", manifest)
        self.assertNotIn("kind: Secret", manifest)
        self.assertNotIn("stringData:", manifest)

    def test_quadlet_preserves_operational_contract(self):
        unit = read(RUNTIME / "todo-postgres.kube")
        template = read(MIGRATION / "templates" / "todo-postgres.kube.j2")

        for content in (unit, template):
            self.assertIn("PodmanArgs=--no-pod-prefix", content)
            self.assertIn(
                "ExecStartPost=/usr/bin/podman update "
                "--health-on-failure=kill todo-postgres",
                content,
            )
            self.assertIn("ExitCodePropagation=any", content)
            self.assertIn("Restart=on-failure", content)
            self.assertIn("todo-postgres-data-volume.service", content)
            self.assertIn("todo-postgres-backup-volume.service", content)
        self.assertIn("todo_kube_database_node_address", template)

    def test_migration_preserves_source_before_stopping_and_never_removes_volumes(self):
        tasks = read(MIGRATION / "tasks" / "main.yml")
        preserve = tasks.index(
            "- name: Preserve the installed database container Quadlet for rollback"
        )
        stop_application = tasks.index(
            "- name: Stop the application tier before database replacement"
        )
        stop_database = tasks.index(
            "- name: Stop the per-container PostgreSQL service"
        )
        remove_source = tasks.index(
            "- name: Remove the replaced PostgreSQL container Quadlet"
        )
        relabel = tasks.index(
            "- name: Apply the shared SELinux label required by rootless Kube volumes"
        )

        self.assertLess(preserve, stop_application)
        self.assertLess(stop_application, stop_database)
        self.assertLess(stop_database, relabel)
        self.assertLess(relabel, remove_source)
        self.assertLess(stop_database, remove_source)
        self.assertIn("force: true", tasks[preserve:stop_application])
        self.assertIn("todo_confirm_postgres_kube_migration", tasks)
        self.assertIn("--no-pod-prefix", tasks)
        self.assertIn("--health-on-failure", tasks)
        self.assertIn("podman, healthcheck, run, todo-postgres", tasks)
        self.assertNotIn("--condition=healthy", tasks)
        self.assertNotIn("podman, volume, rm", tasks)
        self.assertIn("Refusing recursive SELinux relabel", tasks)
        self.assertIn("--type=container_file_t", tasks)
        self.assertIn("--range=s0", tasks)
        self.assertNotIn("chmod", tasks)
        self.assertNotIn("chown", tasks)

    def test_migration_verifies_identity_replication_archive_and_stable_name(self):
        tasks = read(MIGRATION / "tasks" / "main.yml")

        self.assertIn("todo_kube_database_system_id_before", tasks)
        self.assertIn("streaming|async|0|t|reserved", tasks)
        self.assertIn("SELECT pg_walfile_name(pg_current_wal_lsn());", tasks)
        self.assertIn("SELECT pg_switch_wal();", tasks)
        self.assertIn("podman, pod, exists, todo-postgres", tasks)
        self.assertIn("podman, container, exists, todo-postgres", tasks)
        self.assertIn("['HealthcheckOnFailureAction'] == 'kill'", tasks)

    def test_rollback_requires_inputs_before_stop_and_preserves_volumes(self):
        tasks = read(ROLLBACK / "tasks" / "main.yml")
        require = tasks.index("- name: Require complete database rollback inputs")
        stop = tasks.index("- name: Stop the application tier before database rollback")
        restore = tasks.index("- name: Restore the preserved PostgreSQL container Quadlet")

        self.assertLess(require, stop)
        self.assertLess(stop, restore)
        self.assertIn("todo_confirm_postgres_quadlet_rollback", tasks)
        self.assertIn("podman, healthcheck, run, todo-postgres", tasks)
        self.assertNotIn("--condition=healthy", tasks)
        self.assertNotIn("podman, volume, rm", tasks)
        self.assertNotIn("state: absent\n  loop:\n    - todo-postgres-data", tasks)


if __name__ == "__main__":
    unittest.main()
