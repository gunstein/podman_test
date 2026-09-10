"""Protect PVC creation, operational consumers and non-destructive shutdown."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from tests.runtime_fixture import ROOT, RUNTIME

VOLUMES = {"todo-postgres-data", "todo-postgres-backup", "todo-nginx-data"}


def tasks(role):
    return yaml.safe_load((ROOT / f"ansible/roles/{role}/tasks/main.yml").read_text())


def ansible_probe(directory, task_list, variables):
    play = directory / "probe.yml"
    play.write_text(yaml.safe_dump([{
        "name": "Exercise storage contract", "hosts": "localhost", "gather_facts": False,
        "vars": variables, "tasks": task_list,
    }]))
    return subprocess.run(
        [os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook"),
         "-i", "localhost,", "-c", "local", str(play)],
        capture_output=True, text=True, timeout=60,
    )


class PVCStorageTests(unittest.TestCase):
    def test_claims_resolve_to_expected_container_paths_and_owners(self):
        for path in RUNTIME.glob("*.yaml"):
            for delimiter in ("{{", "{%", "{#"):
                self.assertNotIn(delimiter, path.read_text(), str(path))
        for file, pod_name, expected in (
            ("postgres.yaml", "todo-postgres", {
                "todo-postgres-data": ("/var/lib/postgresql/data", 999),
                "todo-postgres-backup": ("/var/lib/postgresql/backup", 999),
            }),
            ("shared-proxy.yaml", "shared-proxy", {
                "todo-nginx-data": ("/var/lib/todo-tls", 101),
            }),
        ):
            docs = list(yaml.safe_load_all((RUNTIME / file).read_text()))
            claims = {d["metadata"]["name"]: d for d in docs
                      if d["kind"] == "PersistentVolumeClaim"}
            self.assertEqual(set(claims), set(expected))
            pod = next(d for d in docs if d["kind"] == "Pod")
            self.assertEqual(pod["metadata"]["name"], pod_name)
            container = pod["spec"]["containers"][0]
            mounts = {m["name"]: m for m in container["volumeMounts"]}
            resolved = {}
            for volume in pod["spec"]["volumes"]:
                self.assertNotIn("hostPath", volume)
                if "persistentVolumeClaim" in volume:
                    claim = volume["persistentVolumeClaim"]["claimName"]
                    mount = mounts[volume["name"]]
                    self.assertFalse(mount.get("readOnly", False))
                    resolved[claim] = mount["mountPath"]
            self.assertEqual(resolved, {name: path for name, (path, uid) in expected.items()})
            for name, (path, uid) in expected.items():
                self.assertEqual(claims[name]["metadata"]["annotations"], {
                    "volume.podman.io/uid": str(uid), "volume.podman.io/gid": str(uid),
                })
                self.assertEqual(container["securityContext"]["runAsUser"], uid)
                self.assertEqual(container["securityContext"]["runAsGroup"], uid)

    def test_no_volume_units_are_installed_or_required(self):
        for path in (ROOT / "ansible/roles").rglob("*.kube.j2"):
            for line in path.read_text().splitlines():
                if line.startswith(("Requires=", "After=", "Wants=")):
                    self.assertTrue(set(line.split("=", 1)[1].split()).isdisjoint(
                        {name + "-volume.service" for name in VOLUMES}), str(path))
                self.assertNotEqual(line, "KubeDownForce=true")
        self.assertEqual(list((ROOT / "quadlet").glob("*.volume")), [])
        for path in (ROOT / "ansible/roles").rglob("*.yml"):
            # Cleanup may name retired definitions, but no role may install them.
            def check(node):
                if isinstance(node, list):
                    for item in node:
                        check(item)
                elif isinstance(node, dict):
                    for module in ("ansible.builtin.copy", "ansible.builtin.template"):
                        if module in node:
                            self.assertNotIn(".volume", str(node), str(path))
                    unit = node.get("ansible.builtin.systemd_service", {})
                    self.assertNotIn(unit.get("name"),
                                     {n + "-volume.service" for n in VOLUMES})
                    for value in node.values():
                        check(value)
            check(yaml.safe_load(path.read_text()))

    def test_standby_creation_plays_only_canonical_data_pvc_before_basebackup(self):
        canonical = next(d for d in yaml.safe_load_all((RUNTIME / "postgres.yaml").read_text())
                         if d["metadata"]["name"] == "todo-postgres-data")
        for role in ("postgres_standby", "postgres_reseed_standby"):
            steps = tasks(role)
            commands = [t.get("ansible.builtin.command", {}).get("argv", []) for t in steps]
            creation = commands.index(["podman", "kube", "play", "-"])
            backup = next(i for i, argv in enumerate(commands) if "pg_basebackup" in argv)
            self.assertLess(creation, backup)
            self.assertNotIn(["podman", "volume", "create", "todo-postgres-data"], commands)
            if role == "postgres_reseed_standby":
                removals = [argv for argv in commands if argv[:3] == ["podman", "volume", "rm"]]
                self.assertEqual(removals, [["podman", "volume", "rm", "todo-postgres-data"]])
                self.assertLess(commands.index(removals[0]), creation)
            else:
                guard = next(i for i, t in enumerate(steps)
                             if "standby_volume_before_bootstrap.rc == 1"
                             in t.get("ansible.builtin.assert", {}).get("that", []))
                self.assertLess(guard, creation)
            with tempfile.TemporaryDirectory() as directory:
                tmp = Path(directory)
                task = copy.deepcopy(steps[creation])
                # Run the actual Ansible stdin expression, replacing only Podman
                # with a recorder. This test cannot create or delete real volumes.
                output = tmp / "claim.yaml"
                task["ansible.builtin.command"]["argv"] = [
                    sys.executable, "-c",
                    "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read())",
                    str(output),
                ]
                result = ansible_probe(tmp, [task],
                                       {"todo_rendered_manifest_directory": str(RUNTIME)})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(list(yaml.safe_load_all(output.read_text())), [canonical])

    def test_backup_rejects_missing_wrong_readonly_or_misplaced_mounts(self):
        steps = tasks("postgres_backup")
        gate = next(t for t in steps if t["name"] == "Require the PVC backup volume at the archive path")
        existence = next(i for i, t in enumerate(steps) if
                         t.get("ansible.builtin.command", {}).get("argv") ==
                         ["podman", "volume", "exists", "todo-postgres-backup"])
        helper = next(i for i, t in enumerate(steps) if "todo-postgres-backup:/backup:U,z"
                      in t.get("ansible.builtin.command", {}).get("argv", []))
        self.assertLess(existence, steps.index(gate))
        self.assertLess(steps.index(gate), helper)
        good = {"Type": "volume", "Name": "todo-postgres-backup",
                "Destination": "/var/lib/postgresql/backup", "RW": True}
        for mounts, accepted in [([good], True), ([], False),
                                 ([{**good, "Name": "wrong"}], False),
                                 ([{**good, "Destination": "/wrong"}], False),
                                 ([{**good, "RW": False}], False)]:
            with tempfile.TemporaryDirectory() as directory:
                result = ansible_probe(Path(directory), [gate],
                                       {"m15_postgres_mounts": {"stdout": json.dumps(mounts)}})
                self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)

    def test_uninstall_preserves_database_by_default_and_never_removes_backup(self):
        play = yaml.safe_load((ROOT / "ansible/uninstall.yml").read_text())[0]
        self.assertFalse(play["vars"]["remove_data"])
        removals = [t for t in play["tasks"] if
                    t.get("ansible.builtin.command", {}).get("argv", [])[:3] ==
                    ["podman", "volume", "rm"]]
        self.assertEqual(len(removals), 2)
        database = next(t for t in removals if
                        t["ansible.builtin.command"]["argv"][-1] == "todo-postgres-data")
        self.assertIn("remove_data | bool", database["when"])
        tls = next(t for t in play["tasks"] if t.get("register") == "proxy_data_volumes")
        self.assertEqual(tls["loop"], ["todo-nginx-data", "todo-caddy-data"])
        self.assertNotIn("when", tls)
