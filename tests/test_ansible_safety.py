import pathlib
import unittest

import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class AnsibleSafetyTests(unittest.TestCase):
    def test_final_standby_helper_hands_shared_selinux_label_to_kube(self):
        for role in ("postgres_standby", "postgres_reseed_standby"):
            with self.subTest(role=role):
                tasks = yaml.safe_load(read(f"ansible/roles/{role}/tasks/main.yml"))
                kube = next(i for i, task in enumerate(tasks)
                            if task.get("ansible.builtin.include_role", {}).get("name")
                            == "postgres_kube_runtime")
                helpers = []
                for task in tasks[:kube]:
                    argv = task.get("ansible.builtin.command", {}).get("argv", [])
                    if "--volume" in argv:
                        helpers.append((task, argv[argv.index("--volume") + 1]))
                task, mount = helpers[-1]
                self.assertEqual(mount, "todo-postgres-data:/var/lib/postgresql/data:z")
                self.assertTrue(task["no_log"])
                self.assertIn("todo-replicator-password", task["ansible.builtin.command"]["argv"])

    def test_replication_authentication_precedes_volume_removal(self):
        tasks = read("ansible/roles/postgres_reseed_standby/tasks/main.yml")

        authentication = tasks.index(
            "- name: Authenticate replication before destructive reseed"
        )
        removal = tasks.index(
            "- name: Remove the explicitly confirmed old database volume"
        )

        self.assertLess(authentication, removal)
        self.assertIn("--command=IDENTIFY_SYSTEM;", tasks)

    def test_rebuild_preflight_compares_replication_credentials(self):
        preflight = read("ansible/preflight-standby-rebuild.yml")

        self.assertIn("podman\n          - secret\n          - inspect", preflight)
        self.assertIn(
            "Require identical replication credentials before destructive reseed",
            preflight,
        )

    def test_pipelining_is_project_configuration_not_inventory_data(self):
        configuration = read("ansible.cfg")

        self.assertIn("pipelining = True", configuration)
        for inventory in (
            "ansible/inventory.ini",
            "ansible/inventory-initial.example.ini",
            "ansible/inventory-recovery.example.ini",
        ):
            self.assertNotIn("ansible_pipelining", read(inventory))

    def test_tool_installers_use_central_exact_file_trust(self):
        trust = read("ansible/roles/todo_fapolicyd/tasks/main.yml")
        self.assertIn("base64 --decode", trust)
        self.assertIn("--trust-file", trust)
        self.assertIn("item.dest", trust)
        self.assertNotIn("import pathlib", trust)
        for tasks_file in (
            "ansible/roles/todo_dr/tasks/main.yml",
            "ansible/roles/postgres_backup/tasks/main.yml",
        ):
            tasks = read(tasks_file)
            self.assertIn("name: todo_fapolicyd", tasks)
            self.assertIn("/opt/todo/bin/", tasks)

    def test_backup_uses_capacity_safe_archive_timeout(self):
        playbook = read("ansible/configure-backup.yml")
        tasks = read("ansible/roles/postgres_backup/tasks/main.yml")

        self.assertIn("m15_archive_timeout: 1h", playbook)
        self.assertIn("m15_archive_timeout", tasks)
        self.assertNotIn("archive_timeout = '60s'", tasks)

    def test_cluster_status_preserves_backup_health(self):
        status = read("ansible/cluster-status.yml")

        self.assertIn("current_setting('archive_mode')", status)
        self.assertIn("last_archived_wal", status)
        self.assertIn("last_archived_time >= last_failed_time", status)

    def test_secret_reads_are_direct_and_suppressed(self):
        promoted_tasks = read(
            "ansible/roles/promoted_application/tasks/main.yml"
        )
        secret_block = promoted_tasks.split(
            "- name: Read the existing Keycloak administrator secret", 1
        )[1].split(
            "- name: Obtain a short-lived Keycloak administrator token", 1
        )[0]
        self.assertIn("- secret\n      - inspect", secret_block)
        self.assertIn("--showsecret", secret_block)
        self.assertNotIn("- run", secret_block)
        self.assertIn("no_log: true", secret_block)

    def test_vault_provisioning_is_outside_demo_scope(self):
        for removed_path in (
            "ansible/provision-secrets.yml",
            "ansible/secrets.example.yml",
            "ansible/tasks/provision_secret.yml",
        ):
            self.assertFalse((PROJECT_ROOT / removed_path).exists())

        surfaces = "\n".join(
            read(path)
            for path in (
                "README.md",
                "PROJECT.md",
                "ansible/README.md",
                "docs/SECRETS.md",
                "offline/build-bundle.sh",
                "scripts/build-operations-package.sh",
                ".github/workflows/clean-install.yml",
            )
        )
        self.assertNotIn("ansible-vault", surfaces)
        self.assertNotIn("provision-secrets", surfaces)

    def test_documentation_separates_learning_acceptance_and_history(self):
        for path in (
            "docs/LEARNING-GUIDE.md",
            "docs/LAB-ACCEPTANCE.md",
        ):
            self.assertTrue((PROJECT_ROOT / path).is_file())

        readme = read("README.md")
        self.assertLessEqual(len(readme.splitlines()), 260)
        self.assertIn("docs/LEARNING-GUIDE.md", readme)
        self.assertIn("docs/LAB-ACCEPTANCE.md", readme)
        self.assertIn("Development journal", readme)

    def test_legacy_quadlet_retirement_is_explicitly_gated(self):
        guide = read("quadlet/QUADLET-REFERENCE.md")

        self.assertIn("source for the accepted deployment", guide)
        self.assertIn("rollback\nboundary", guide)
        self.assertIn("not the target architecture", guide)
        self.assertIn("quadlet-reference-v1", guide)
        self.assertIn("## Retirement gate", guide)
        for gate in (
            "clean install",
            "cold",
            "reboot",
            "replication",
            "standby rebuild",
            "full DR acceptance",
        ):
            self.assertIn(gate, guide)
        self.assertIn("Remove the seven legacy `.container` files", guide)
        self.assertIn("no production path refers to the legacy files", guide)

    def test_documentation_keeps_operational_safety_boundaries(self):
        operational_docs = "\n".join(
            read(path)
            for path in (
                "README.md",
                "ansible/STANDBY-ARCHITECTURE.md",
                "ansible/STANDBY-BOOTSTRAP.md",
                "ansible/PROMOTION.md",
                "ansible/APPLICATION-FAILOVER.md",
                "ansible/BACKUP-PITR.md",
                "ansible/RESTORE-REDUNDANCY.md",
            )
        )
        self.assertNotIn("future from-zero", operational_docs.lower())
        self.assertNotIn("planned for M15", operational_docs)
        self.assertNotIn("This is learning stage", operational_docs)

        for builder in (
            "offline/build-bundle.sh",
            "scripts/build-operations-package.sh",
        ):
            content = read(builder)
            self.assertIn("LEARNING-GUIDE.md", content)
            self.assertIn("LAB-ACCEPTANCE.md", content)

    def test_offline_packages_record_source_revision(self):
        for builder_path, package_name in (
            ("offline/build-bundle.sh", "todo-offline-m12"),
            ("scripts/build-operations-package.sh", "todo-operations"),
        ):
            builder = read(builder_path)
            self.assertIn(f"package={package_name}", builder)
            self.assertIn("source_revision=", builder)
            self.assertIn("source_state=", builder)
            self.assertIn("rev-parse --verify HEAD", builder)

    def test_operational_surface_has_two_inventories_and_one_package_builder(self):
        inventories = sorted(
            path.name for path in (PROJECT_ROOT / "ansible").glob("inventory-*.example.ini")
        )
        builders = sorted(
            path.name for path in (PROJECT_ROOT / "scripts").glob("build-*-package.sh")
        )

        self.assertEqual(
            inventories,
            ["inventory-initial.example.ini", "inventory-recovery.example.ini"],
        )
        self.assertEqual(builders, ["build-operations-package.sh"])

    def test_operations_package_contains_resumable_dr_runner(self):
        builder = read("scripts/build-operations-package.sh")
        self.assertIn("ansible/DR-AUTOMATION.md", builder)
        self.assertIn("scripts/todo_dr_run.py", builder)

    def test_rebuild_installs_role_reversed_dr_configuration(self):
        rebuild = read("ansible/rebuild-standby.yml")
        dr_role = read("ansible/roles/todo_dr/tasks/main.yml")
        self.assertIn("- role: todo_dr", rebuild)
        self.assertIn("todo_dr_primary_group: todo_current_primary", rebuild)
        self.assertIn("todo_dr_standby_group: todo_rebuild_standby", rebuild)
        self.assertIn("todo_dr_primary_group | default(", dr_role)

    def test_active_postgres_operations_are_kube_native(self):
        for tasks_file in (
            "ansible/roles/postgres_backup/tasks/main.yml",
            "ansible/roles/postgres_primary/tasks/main.yml",
            "ansible/roles/postgres_standby/tasks/main.yml",
            "ansible/roles/postgres_redundancy_primary/tasks/main.yml",
            "ansible/roles/postgres_reseed_standby/tasks/main.yml",
        ):
            tasks = read(tasks_file)
            self.assertNotIn("src: todo-postgres.container.j2", tasks)
            self.assertNotIn("dest: todo-postgres.container", tasks)

        backup = read("ansible/roles/postgres_backup/tasks/main.yml")
        redundancy = read(
            "ansible/roles/postgres_redundancy_primary/tasks/main.yml"
        )
        self.assertIn("todo-app.service", backup)
        self.assertIn("todo-app.service", redundancy)
        self.assertNotIn("else 'todo-frontend.service'", backup)
        self.assertNotIn("else 'todo-frontend.service'", redundancy)

    def test_transition_rollback_remains_separate_from_active_runtime(self):
        rollback = read(
            "ansible/roles/kube_postgres_primary_rollback/tasks/main.yml"
        )
        self.assertIn("todo-postgres.container", rollback)


if __name__ == "__main__":
    unittest.main()
