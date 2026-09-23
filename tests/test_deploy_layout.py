"""Exercise environment rendering and inventory resolution after source relocation."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class DeployLayoutTests(unittest.TestCase):
    def test_renderer_works_outside_checkout_and_applies_shared_environment(self):
        for environment, hostname in (("local", "todo.test"), ("prod", "todo.test")):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "rendered"
                subprocess.run([
                    str(ROOT / "deploy/scripts/render-kube-runtime.sh"),
                    str(ROOT / f"deploy/environments/{environment}/values.yaml"), str(output),
                ], cwd=directory, check=True, capture_output=True)
                self.assertEqual({p.name for p in output.iterdir()}, {
                    "app.yaml", "keycloak.yaml", "postgres.yaml", "config.yaml", "shared-proxy.yaml",
                    "notes-app.yaml", "notes-postgres.yaml", "notes-config.yaml",
                    "keycloak-postgres.yaml", "keycloak-config.yaml",
                })
                config = list(yaml.safe_load_all((output / "config.yaml").read_text()))
                proxy = list(yaml.safe_load_all((output / "shared-proxy.yaml").read_text()))
                proxy_env = next(d for d in proxy if d["metadata"]["name"] == "shared-nginx-env")
                self.assertEqual(proxy_env["data"]["TODO_TLS_HOSTNAME"], hostname)
                self.assertIn(f"https://{hostname}:8443", str(config))
                pods = [d for p in output.glob("*.yaml") for d in yaml.safe_load_all(p.read_text())
                        if d["kind"] == "Pod"]
                self.assertEqual({d["metadata"]["name"] for d in pods}, {
                    "todo-app", "todo-postgres", "notes-app", "notes-postgres", "keycloak",
                    "keycloak-postgres", "shared-proxy",
                })

    def test_inventory_loads_adjacent_group_vars_without_playbook_context(self):
        executable = os.environ.get("ANSIBLE_INVENTORY_COMMAND", "ansible-inventory")
        expected = {
            "initial": ("todo-primary", "todo_primary"),
            "recovery": ("todo-standby", "todo_current_primary"),
        }
        for topology, (primary, group) in expected.items():
            with self.subTest(topology=topology), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([
                    executable, "--list", "-i",
                    str(ROOT / f"deploy/ansible/inventories/{topology}/hosts.example.ini"),
                ], cwd=directory, check=True, capture_output=True, text=True)
                inventory = json.loads(result.stdout)
                self.assertIn(primary, inventory[group]["hosts"])
                hosts = inventory["_meta"]["hostvars"]
                self.assertEqual(set(hosts), {"todo-primary", "todo-standby"})
                for host in hosts.values():
                    self.assertEqual(host["ansible_user"], "gunstein")
                if topology == "initial":
                    self.assertEqual(hosts[primary]["todo_rpo_target_seconds"], 30)
                else:
                    self.assertEqual(hosts[primary]["todo_bundle_directory"],
                                     "/home/gunstein/todo-offline-m12")
                    self.assertEqual(hosts["todo-primary"]["todo_rebuild_replication_slot"],
                                     "todo_rebuilt_standby")

    def test_manual_recipes_customize_effective_inventory_accounts_and_paths(self):
        executable = os.environ.get("ANSIBLE_INVENTORY_COMMAND", "ansible-inventory")
        cases = (
            ("03-DR-TWO-VM.md", "initial", "192.168.1.51"),
            ("03-DR-TWO-VM.md", "recovery", "192.168.1.51"),
            ("04-BACKUP-PITR.md", "recovery", "192.168.1.50"),
        )
        for recipe, topology, standby_address in cases:
            with self.subTest(recipe=recipe, topology=topology), \
                    tempfile.TemporaryDirectory() as directory:
                relative = Path("deploy/ansible/inventories") / topology
                source = ROOT / relative
                target = Path(directory) / relative
                target.mkdir(parents=True)
                shutil.copyfile(source / "hosts.example.ini", target / "hosts.ini")
                shutil.copytree(source / "group_vars", target / "group_vars")
                document = (ROOT / "docs/manual-recipes" / recipe).read_text()
                blocks = [block for block in re.findall(r"```bash\n(.*?)```", document, re.S)
                          if block.startswith("sed -i")
                          and f"{relative}/hosts.ini" in block]
                self.assertEqual(len(blocks), 1, "Expected one inventory customization block")
                # Execute the published commands, not a second implementation of the edits.
                subprocess.run(["bash", "-eu", "-c", blocks[0]], cwd=directory,
                               check=True, capture_output=True, text=True)
                result = subprocess.run([executable, "--list", "-i", str(target / "hosts.ini")],
                                        cwd=directory, check=True, capture_output=True, text=True)
                hosts = json.loads(result.stdout)["_meta"]["hostvars"]
                self.assertEqual(set(hosts), {"todo-primary", "todo-standby"})
                self.assertEqual(hosts["todo-standby"]["todo_node_address"], standby_address)
                if recipe == "03-DR-TWO-VM.md":
                    self.assertEqual(hosts["todo-primary"]["todo_node_address"], "192.168.1.50")
                for host in hosts.values():
                    self.assertEqual(host["ansible_user"], "todo")
                    self.assertNotIn("/home/gunstein", json.dumps(host))
                    if topology == "recovery":
                        self.assertEqual(host["todo_user_home"], "/home/todo")
                if topology == "recovery":
                    self.assertEqual(hosts["todo-standby"]["todo_bundle_directory"],
                                     "/home/todo/todo-offline-m12")
