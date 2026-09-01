import pathlib
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class AnsibleSafetyTests(unittest.TestCase):
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

    def test_tool_installers_do_not_embed_python_in_role_yaml(self):
        for tasks_file in (
            "ansible/roles/todo_dr/tasks/main.yml",
            "ansible/roles/postgres_backup/tasks/main.yml",
        ):
            tasks = read(tasks_file)
            self.assertNotIn("import pathlib", tasks)
            self.assertIn("base64 --decode", tasks)

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


if __name__ == "__main__":
    unittest.main()
