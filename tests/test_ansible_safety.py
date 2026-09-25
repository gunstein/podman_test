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
        authenticate = index(lambda task: task.get("vars", {}).get("todo_replication_operation") == "authenticate")
        reseed = index(lambda task: task.get("vars", {}).get("todo_replication_operation") == "reseed")

        self.assertLess(quarantine, removal)
        self.assertLess(removal, reseed)
        # reseed_standby only authenticates its own database right before deleting
        # it (replication.py); a group-wide authenticate task here, before any
        # database is touched, is what actually stops a partial rebuild when one
        # database's replication port or credential is not ready.
        self.assertLess(authenticate, reseed)
        self.assertEqual(
            tasks[authenticate]["loop"], tasks[reseed]["loop"],
            "the authenticate gate must cover the exact same registered group as reseed",
        )
        self.assertEqual(
            tasks[reseed]["vars"]["todo_replication_primary_address"],
            "{{ hostvars[groups['todo_current_primary'][0]].todo_node_address }}",
        )
        self.assertEqual(
            tasks[authenticate]["vars"]["todo_replication_primary_address"],
            tasks[reseed]["vars"]["todo_replication_primary_address"],
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

    def test_backup_role_is_transport_for_the_backup_tool(self):
        # Archive gates, settings, the single restart and WAL verification live in
        # todo_backup.py configure; tests/test_todo_backup.py exercises them.
        tasks = yaml.safe_load(read("deploy/ansible/roles/postgres_backup/tasks/main.yml"))
        names = [task["name"] for task in tasks]
        promotion = names.index("Require completed promotion for the entire writable group")
        install = names.index("Install and trust the backup and PITR tool")
        configure = names.index("Configure and verify continuous WAL archiving for the complete group")
        self.assertLess(promotion, install)
        self.assertLess(install, configure)
        self.assertEqual(tasks[configure]["ansible.builtin.command"]["argv"][1:3],
                         ["/opt/todo/bin/todo_backup.py", "configure"])
        self.assertFalse((PROJECT_ROOT / "deploy/ansible/roles/postgres_backup/tasks/database.yml").exists())

    def test_cluster_status_preserves_backup_health(self):
        status = read("deploy/ansible/tasks/cluster-status-primary.yml")

        self.assertIn("current_setting('archive_mode')", status)
        self.assertIn("last_archived_wal", status)
        self.assertIn("last_archived_time >= last_failed_time", status)

    def test_cluster_status_checks_every_registered_database(self):
        playbook = yaml.safe_load(read("deploy/ansible/playbooks/cluster-status.yml"))
        for play in playbook:
            loops = [task for task in play["tasks"] if "loop" in task]
            self.assertEqual(len(loops), 1, play["name"])
            self.assertEqual(loops[0]["loop"], "{{ todo_status_databases }}")
        registry = read("deploy/ansible/tasks/cluster-status-registry.yml")
        self.assertIn("replication-apps, --details", registry)
        self.assertNotIn("become", registry)
        for name in ("cluster-status-primary.yml", "cluster-status-standby.yml"):
            tasks = read("deploy/ansible/tasks/" + name)
            self.assertNotIn("todo-postgres", tasks)
            self.assertIn("todo_status_db.postgres_container", tasks)
        self.assertIn("todo_status_db.rebuild_slot", read("deploy/ansible/tasks/cluster-status-primary.yml"))

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
            "deploy/ansible/tasks/publish-primaries.yml",
        ):
            tasks = read(tasks_file)
            self.assertNotIn("src: todo-postgres.container.j2", tasks)
            self.assertNotIn("dest: todo-postgres.container", tasks)

        # Application-tier restarts after a database restart are owned by Python:
        # todo_backup.py configure and app_installer publish-primaries, both tested there.
        for role, mode in (("postgres_primary", "bootstrap"), ("postgres_redundancy_primary", "redundancy")):
            tasks = yaml.safe_load(read(f"deploy/ansible/roles/{role}/tasks/main.yml"))
            publish = [task for task in tasks if str(task.get("ansible.builtin.include_tasks", "")).endswith(
                "/tasks/publish-primaries.yml")]
            self.assertEqual([task["vars"]["todo_publish_primaries_mode"] for task in publish], [mode], role)


if __name__ == "__main__":
    unittest.main()
