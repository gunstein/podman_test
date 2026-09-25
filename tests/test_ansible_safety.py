import pathlib
import unittest

import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class AnsibleSafetyTests(unittest.TestCase):
    def test_reseed_role_is_transport_for_the_group_reseed(self):
        # app_installer reseed-group owns the quarantine, every local check and
        # primary authentication before the first deletion, and each reseed:
        # deploy/installer/tests/test_replication.py ReseedGroupTests.
        tasks = yaml.safe_load(read("deploy/ansible/roles/postgres_reseed_standby/tasks/main.yml"))
        commands = [task for task in tasks if "ansible.builtin.command" in task]
        self.assertEqual(len(commands), 1)
        argv = commands[0]["ansible.builtin.command"]["argv"]
        self.assertEqual(argv[:4], ["python3", "-m", "app_installer", "reseed-group"])
        option = dict(zip(argv[4::2], argv[5::2]))
        self.assertEqual(option["--primary-address"],
                         "{{ hostvars[groups['todo_current_primary'][0]].todo_node_address }}")
        self.assertEqual(option["--confirm-fenced"], "{{ todo_confirm_old_primary_fenced | default('') }}")
        self.assertEqual(option["--confirm-reseed"], "{{ todo_confirm_reseed | default('') }}")
        # The per-database reseed operation is gone: rebuild never acts on a partial group.
        self.assertNotIn("'reseed'", read("deploy/ansible/tasks/replicate-workload.yml"))
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
        role = read("deploy/ansible/roles/todo_fapolicyd/tasks/main.yml")
        script = read("deploy/scripts/trust-files.sh")
        # tests/test_trust_files.py runs the script and the real role.
        self.assertIn("base64 --decode", script)
        self.assertIn("--trust-file", script)
        self.assertIn("/deploy/scripts/trust-files.sh", role)
        self.assertIn("item.dest", role)
        self.assertNotIn("ansible.builtin.script", role)
        self.assertNotIn("import pathlib", role)
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

    def test_cluster_status_is_read_only_transport_for_both_roles(self):
        # app_installer cluster-status owns the checks: test_replication.py ClusterStatusTests.
        playbook = yaml.safe_load(read("deploy/ansible/playbooks/cluster-status.yml"))
        roles = [task["vars"]["todo_status_role"] for play in playbook for task in play["tasks"]]
        self.assertEqual(roles, ["primary", "standby"])
        check = read("deploy/ansible/tasks/cluster-status-check.yml")
        self.assertIn("[python3, -m, app_installer, cluster-status,", check)
        self.assertNotIn("become", check)
        self.assertNotIn("stage-installer", check)

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
