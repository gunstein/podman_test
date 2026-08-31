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

    def test_m16_preflight_compares_replication_credentials(self):
        preflight = read("ansible/preflight-m16.yml")

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
            "ansible/inventory-m13.example.ini",
            "ansible/inventory-m14.example.ini",
            "ansible/inventory-m16.example.ini",
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

    def test_m15_uses_capacity_safe_archive_timeout(self):
        playbook = read("ansible/configure-backup-m15.yml")
        tasks = read("ansible/roles/postgres_backup/tasks/main.yml")

        self.assertIn("m15_archive_timeout: 1h", playbook)
        self.assertIn("m15_archive_timeout", tasks)
        self.assertNotIn("archive_timeout = '60s'", tasks)

    def test_m16_status_preserves_backup_health(self):
        status = read("ansible/status-m16.yml")

        self.assertIn("current_setting('archive_mode')", status)
        self.assertIn("last_archived_wal", status)
        self.assertIn("last_archived_time >= last_failed_time", status)


if __name__ == "__main__":
    unittest.main()
