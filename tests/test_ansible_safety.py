import pathlib
import unittest

import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class AnsibleSafetyTests(unittest.TestCase):
    def test_reseed_role_quarantines_then_reseeds_every_registered_database(self):
        # replicate-workload.yml is transport only; replication.py owns the SELinux
        # label helper, the authenticate-before-volume-removal ordering, and the
        # credential check (a live authenticated connection), all covered directly
        # by deploy/installer/tests/test_replication.py.
        tasks = yaml.safe_load(read("deploy/ansible/roles/postgres_reseed_standby/tasks/main.yml"))

        def index(match):
            return next(i for i, task in enumerate(tasks) if match(task))

        quarantine = index(lambda task: task.get("vars", {}).get("todo_replication_operation") == "quarantined")
        removal = index(lambda task: "Remove the shared Kube units" in task["name"])
        reseed = index(lambda task: task.get("vars", {}).get("todo_replication_operation") == "reseed")

        self.assertLess(quarantine, removal)
        self.assertLess(removal, reseed)
        self.assertEqual(
            tasks[reseed]["vars"]["todo_replication_primary_address"],
            "{{ hostvars[groups['todo_current_primary'][0]].todo_node_address }}",
        )

    def test_rebuild_preflight_verifies_every_registered_database_before_destructive_reseed(self):
        preflight = read("deploy/ansible/playbooks/preflight-standby-rebuild.yml")

        self.assertIn("todo_replication_operation: rebuild-primary-check", preflight)
        self.assertIn("todo_replication_operation: reseed-check", preflight)
        self.assertIn("todo_replication_operation: quarantined", preflight)
        self.assertIn(
            "Rebuild host must remain infrastructure-fenced",
            preflight,
        )

    def test_pipelining_is_project_configuration_not_inventory_data(self):
        configuration = read("ansible.cfg")

        self.assertIn("pipelining = True", configuration)
        for inventory in (
            "deploy/ansible/inventories/local/hosts.ini",
            "deploy/ansible/inventories/initial/hosts.example.ini",
            "deploy/ansible/inventories/recovery/hosts.example.ini",
        ):
            self.assertNotIn("ansible_pipelining", read(inventory))

    def test_tool_installers_use_central_exact_file_trust(self):
        trust = read("deploy/ansible/roles/todo_fapolicyd/tasks/main.yml")
        self.assertIn("base64 --decode", trust)
        self.assertIn("--trust-file", trust)
        self.assertIn("item.dest", trust)
        self.assertNotIn("import pathlib", trust)
        for tasks_file in (
            "deploy/ansible/roles/todo_dr/tasks/main.yml",
            "deploy/ansible/roles/postgres_backup/tasks/main.yml",
        ):
            tasks = read(tasks_file)
            self.assertIn("name: todo_fapolicyd", tasks)
            self.assertIn("/opt/todo/bin/", tasks)

    def test_backup_uses_capacity_safe_archive_timeout(self):
        playbook = read("deploy/ansible/playbooks/configure-backup.yml")
        tasks = read("deploy/ansible/roles/postgres_backup/tasks/database.yml")

        self.assertIn("m15_archive_timeout: 1h", playbook)
        self.assertIn("m15_archive_timeout", tasks)
        self.assertNotIn("archive_timeout = '60s'", tasks)

    def test_backup_refreshes_local_replication_access_before_base_backup(self):
        tasks = yaml.safe_load(read("deploy/ansible/roles/postgres_backup/tasks/database.yml"))
        hba = next(i for i, task in enumerate(tasks)
                   if task.get("vars", {}).get("todo_replication_operation") == "hba")
        backup = next(i for i, task in enumerate(tasks)
                      if task["name"] == "Require the backup volume created by the PostgreSQL PVC")
        self.assertLess(hba, backup)
        self.assertEqual(tasks[hba]["vars"]["todo_replication_app"], "{{ m15_app.name }}")
        self.assertIn("replicate-workload.yml", tasks[hba]["ansible.builtin.include_tasks"])
        # test_replication executes refresh_hba's actual shell against an inherited
        # configuration and verifies the current subnet, scope and unchanged repeat.

    def test_cluster_status_preserves_backup_health(self):
        status = read("deploy/ansible/playbooks/cluster-status.yml")

        self.assertIn("current_setting('archive_mode')", status)
        self.assertIn("last_archived_wal", status)
        self.assertIn("last_archived_time >= last_failed_time", status)

    def test_vault_provisioning_is_outside_demo_scope(self):
        for removed_path in (
            "deploy/ansible/provision-secrets.yml",
            "deploy/ansible/secrets.example.yml",
            "deploy/ansible/tasks/provision_secret.yml",
        ):
            self.assertFalse((PROJECT_ROOT / removed_path).exists())

        surfaces = "\n".join(
            read(path)
            for path in (
                "README.md",
                "PROJECT.md",
                "deploy/ansible/README.md",
                "docs/SECRETS.md",
                "deploy/offline/build-bundle.sh",
                "deploy/scripts/build-operations-package.sh",
                ".github/workflows/clean-install.yml",
            )
        )
        self.assertNotIn("ansible-vault", surfaces)
        self.assertNotIn("provision-secrets", surfaces)

    def test_offline_packages_record_source_revision(self):
        for builder_path, package_name in (
            ("deploy/offline/build-bundle.sh", "todo-offline-m12"),
            ("deploy/scripts/build-operations-package.sh", "todo-operations"),
        ):
            builder = read(builder_path)
            self.assertIn(f"package={package_name}", builder)
            self.assertIn("source_revision=", builder)
            self.assertIn("source_state=", builder)
            self.assertIn("rev-parse --verify HEAD", builder)

    def test_operational_surface_has_two_inventories_and_one_package_builder(self):
        inventories = sorted(
            path.parent.name for path in (PROJECT_ROOT / "deploy/ansible/inventories").glob("*/hosts.example.ini")
        )
        builders = sorted(
            path.name for path in (PROJECT_ROOT / "deploy/scripts").glob("build-*-package.sh")
        )

        self.assertEqual(
            inventories,
            ["initial", "recovery"],
        )
        self.assertEqual(builders, ["build-operations-package.sh"])

    def test_rebuild_installs_role_reversed_dr_configuration(self):
        rebuild = read("deploy/ansible/playbooks/rebuild-standby.yml")
        dr_role = read("deploy/ansible/roles/todo_dr/tasks/main.yml")
        self.assertIn("- role: todo_dr", rebuild)
        self.assertIn("todo_dr_primary_group: todo_current_primary", rebuild)
        self.assertIn("todo_dr_standby_group: todo_rebuild_standby", rebuild)
        self.assertIn("todo_dr_primary_group | default(", dr_role)

    def test_active_postgres_operations_are_kube_native(self):
        for tasks_file in (
            "deploy/ansible/roles/postgres_backup/tasks/main.yml",
            "deploy/ansible/roles/postgres_primary/tasks/main.yml",
            "deploy/ansible/roles/postgres_standby/tasks/main.yml",
            "deploy/ansible/roles/postgres_redundancy_primary/tasks/main.yml",
            "deploy/ansible/roles/postgres_reseed_standby/tasks/main.yml",
        ):
            tasks = read(tasks_file)
            self.assertNotIn("src: todo-postgres.container.j2", tasks)
            self.assertNotIn("dest: todo-postgres.container", tasks)

        backup = read("deploy/ansible/roles/postgres_backup/tasks/main.yml")
        redundancy = read("deploy/ansible/roles/postgres_redundancy_primary/tasks/main.yml")
        self.assertIn("map(attribute='application_service')", backup)
        self.assertIn("map(attribute='application_service')", redundancy)
        for role in ("postgres_primary", "postgres_backup", "postgres_redundancy_primary"):
            tasks = yaml.safe_load(read(f"deploy/ansible/roles/{role}/tasks/" +
                                          ({"postgres_primary": "primary.yml", "postgres_backup": "database.yml",
                                            "postgres_redundancy_primary": "primary.yml"}.get(role, "main.yml"))))
            starts = [task["ansible.builtin.systemd_service"].get("name")
                      for task in tasks if task.get("ansible.builtin.systemd_service", {}).get("state") == "started"]
            self.assertIn("shared-proxy.service", starts, role)

        self.assertNotIn("else 'todo-frontend.service'", backup)
        self.assertNotIn("else 'todo-frontend.service'", redundancy)


if __name__ == "__main__":
    unittest.main()
