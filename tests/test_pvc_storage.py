"""Protect PVC creation, operational consumers and non-destructive shutdown."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from tests.runtime_fixture import ROOT, RUNTIME

VOLUMES = {"todo-postgres-data", "todo-postgres-backup", "todo-nginx-data",
           "notes-postgres-data", "notes-postgres-backup"}


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
            ("notes-postgres.yaml", "notes-postgres", {
                "notes-postgres-data": ("/var/lib/postgresql/data", 999),
                "notes-postgres-backup": ("/var/lib/postgresql/backup", 999),
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
        for path in (ROOT / "deploy/quadlet").glob("*.kube.j2"):
            for line in path.read_text().splitlines():
                if line.startswith(("Requires=", "After=", "Wants=")):
                    self.assertTrue(set(line.split("=", 1)[1].split()).isdisjoint(
                        {name + "-volume.service" for name in VOLUMES}), str(path))
                self.assertNotEqual(line, "KubeDownForce=true")
        self.assertEqual(list((ROOT / "deploy/quadlet").glob("*.volume")), [])
        for path in (ROOT / "deploy/ansible/roles").rglob("*.yml"):
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

    def test_python_standby_consumes_the_actual_rendered_data_claim(self):
        # replicate-workload.yml is transport only; replication.py's data_claim
        # (used identically by bootstrap_standby and reseed_standby) owns playing
        # only the canonical PVC before pg_basebackup, covered directly by
        # deploy/installer/tests/test_replication.py.
        from todo_installer import apps, replication
        canonical = next(d for d in yaml.safe_load_all((RUNTIME / "postgres.yaml").read_text())
                         if d["metadata"]["name"] == "todo-postgres-data")
        self.assertEqual(yaml.safe_load(replication.data_claim(apps.IDENTITY_DATABASE_APP, RUNTIME)), canonical)
        role = (ROOT / "deploy/ansible/roles/postgres_standby/tasks/main.yml").read_text()
        self.assertIn('replicate-workload.yml', role)
        self.assertIn('todo_replication_operation: standby', role)
        reseed_role = (ROOT / "deploy/ansible/roles/postgres_reseed_standby/tasks/main.yml").read_text()
        self.assertIn('replicate-workload.yml', reseed_role)
        self.assertIn('todo_replication_operation: reseed', reseed_role)

    def test_backup_rejects_missing_wrong_readonly_or_misplaced_mounts(self):
        from todo_installer import apps
        steps = yaml.safe_load((ROOT / 'deploy/ansible/roles/postgres_backup/tasks/database.yml').read_text())
        gate = next(t for t in steps if t["name"] == "Require the PVC backup volume at the archive path")
        existence = next(i for i, t in enumerate(steps) if
                         t.get("ansible.builtin.command", {}).get("argv") ==
                         ["podman", "volume", "exists", "{{ m15_app.backup_volume }}"])
        helper = next(i for i, t in enumerate(steps) if "{{ m15_app.backup_volume }}:/backup:U,z"
                      in t.get("ansible.builtin.command", {}).get("argv", []))
        self.assertLess(existence, steps.index(gate))
        self.assertLess(steps.index(gate), helper)
        for app in apps.REPLICATED_APPS:
            good = {"Type": "volume", "Name": app.volume('backup'),
                    "Destination": "/var/lib/postgresql/backup", "RW": True}
            other = next(candidate for candidate in apps.REPLICATED_APPS if candidate != app)
            for mounts, accepted in [([good], True), ([], False), ([good, good], False),
                                     ([{**good, "Name": other.volume('backup')}], False),
                                     ([{**good, "Destination": "/wrong"}], False),
                                     ([{**good, "RW": False}], False)]:
                with self.subTest(app=app.name, mounts=mounts), tempfile.TemporaryDirectory() as directory:
                    result = ansible_probe(Path(directory), [gate], {
                        "m15_app": apps.describe(app),
                        "m15_postgres_mounts": {"stdout": json.dumps(mounts)}})
                    self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)

    def test_uninstall_preserves_database_and_tls_data_by_default_and_never_removes_backup(self):
        from todo_installer import uninstall
        for remove_data in (False, True):
            with tempfile.TemporaryDirectory() as directory, \
                    patch("todo_installer.uninstall.exists",
                          side_effect=lambda kind, name: not name.endswith("-replicator-password")), \
                    patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                uninstall.uninstall(remove_data=remove_data, quadlet_dir=directory)
                commands = [call.args[0] for call in run.call_args_list]
                volumes = [argv[-1] for argv in commands if argv[:3] == ["podman", "volume", "rm"]]
                # todo-nginx-data holds the demo CA and is documented as persistent
                # like the database volumes, so it only goes with --remove-data too.
                self.assertEqual(set(volumes),
                                 ({"todo-postgres-data", "notes-postgres-data", "keycloak-postgres-data",
                                   "todo-nginx-data", "todo-caddy-data"} if remove_data else set()))
                for backup in ("todo-postgres-backup", "notes-postgres-backup", "keycloak-postgres-backup"):
                    self.assertNotIn(backup, volumes)
                secrets = [argv[-1] for argv in commands if argv[:3] == ["podman", "secret", "rm"]]
                self.assertEqual(set(secrets), set(uninstall.SECRETS) if remove_data else set())
