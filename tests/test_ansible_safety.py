import pathlib
import unittest

import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class AnsibleSafetyTests(unittest.TestCase):
    def test_final_standby_helper_hands_shared_selinux_label_to_kube(self):
        for role in ("postgres_reseed_standby",):
            with self.subTest(role=role):
                tasks = yaml.safe_load(read(f"deploy/ansible/roles/{role}/tasks/main.yml"))
                kube = next(
                    i
                    for i, task in enumerate(tasks)
                    if task.get("vars", {}).get("todo_installer_workload") == "postgres"
                )
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
        tasks = read("deploy/ansible/roles/postgres_reseed_standby/tasks/main.yml")

        authentication = tasks.index("- name: Authenticate replication before destructive reseed")
        removal = tasks.index("- name: Remove the explicitly confirmed old database volume")

        self.assertLess(authentication, removal)
        self.assertIn("--command=IDENTIFY_SYSTEM;", tasks)

    def test_rebuild_preflight_compares_replication_credentials(self):
        preflight = read("deploy/ansible/playbooks/preflight-standby-rebuild.yml")

        self.assertIn("podman\n          - secret\n          - inspect", preflight)
        self.assertIn(
            "Require identical replication credentials before destructive reseed",
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
        tasks = read("deploy/ansible/roles/postgres_backup/tasks/main.yml")

        self.assertIn("m15_archive_timeout: 1h", playbook)
        self.assertIn("m15_archive_timeout", tasks)
        self.assertNotIn("archive_timeout = '60s'", tasks)

    def test_backup_refreshes_local_replication_access_before_base_backup(self):
        # A promoted host inherits pg_hba.conf from whichever host it last
        # streamed from, so its own rootless subnet is not yet permitted to
        # open a local replication connection. Base backups use the
        # replication protocol, so this must be fixed before any backup
        # directory or archive task, using the host's own current subnet
        # rather than a value inherited through basebackup/WAL replay.
        tasks = yaml.safe_load(read("deploy/ansible/roles/postgres_backup/tasks/main.yml"))
        names = [task["name"] for task in tasks]

        hba_fix = names.index("Allow local replication access for base backups")
        backup_volume = names.index(
            "Require the backup volume created by the PostgreSQL PVC"
        )
        self.assertLess(hba_fix, backup_volume)

        subnet_task = tasks[names.index("Read the local rootless port-proxy subnet")]
        self.assertIn(
            "podman network inspect app-network",
            " ".join(tasks[names.index("Inspect the local rootless network")]
                     ["ansible.builtin.command"]["argv"]),
        )
        self.assertIn("subnets", subnet_task["ansible.builtin.set_fact"]["m15_local_rootless_subnet"])

        fix_task = tasks[hba_fix]
        self.assertIn("todo_replicator", str(fix_task["ansible.builtin.command"]["argv"]))
        self.assertIn("m15_local_rootless_subnet", str(fix_task["ansible.builtin.command"]["argv"]))
        self.assertEqual(fix_task["changed_when"], "m15_replication_hba.stdout | trim == 'changed'")

        reload_task = tasks[names.index(
            "Reload authentication configuration after local replication access change"
        )]
        self.assertEqual(reload_task["when"], "m15_replication_hba.changed")

    def test_cluster_status_preserves_backup_health(self):
        status = read("deploy/ansible/playbooks/cluster-status.yml")

        self.assertIn("current_setting('archive_mode')", status)
        self.assertIn("last_archived_wal", status)
        self.assertIn("last_archived_time >= last_failed_time", status)

    def test_secret_reads_are_direct_and_suppressed(self):
        promoted_tasks = read("deploy/ansible/roles/promoted_application/tasks/main.yml")
        secret_block = promoted_tasks.split(
            "- name: Read the existing Keycloak administrator secret", 1
        )[1].split("- name: Obtain a short-lived Keycloak administrator token", 1)[0]
        self.assertIn("- secret\n      - inspect", secret_block)
        self.assertIn("--showsecret", secret_block)
        self.assertNotIn("- run", secret_block)
        self.assertIn("no_log: true", secret_block)

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
        self.assertIn("todo-app.service", backup)
        self.assertIn("todo-app.service", redundancy)
        for role in ("postgres_primary", "postgres_backup", "postgres_redundancy_primary"):
            tasks = yaml.safe_load(read(f"deploy/ansible/roles/{role}/tasks/" +
                                          ("primary.yml" if role == "postgres_primary" else "main.yml")))
            starts = [task["ansible.builtin.systemd_service"].get("name")
                      for task in tasks if task.get("ansible.builtin.systemd_service", {}).get("state") == "started"]
            self.assertIn("shared-proxy.service", starts, role)

        self.assertNotIn("else 'todo-frontend.service'", backup)
        self.assertNotIn("else 'todo-frontend.service'", redundancy)


if __name__ == "__main__":
    unittest.main()
