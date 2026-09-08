"""Check the actual operations archive, not only its builder's source."""

import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def verify_package(test, archive, prefix):
    subprocess.run(["sha256sum", "-c", archive.name + ".sha256"],
                   cwd=archive.parent, check=True, capture_output=True)
    with tarfile.open(archive) as package:
        files = {member.name.removeprefix(prefix + "/"): package.extractfile(member).read()
                 for member in package.getmembers() if member.isfile()}
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"],
                                    cwd=ROOT, text=True).strip()
    test.assertEqual(files["VERSION"].decode(),
                     f"package={prefix}\nsource_revision={revision}\n"
                     f"source_state={'dirty' if dirty else 'clean'}\n")
    checksums = {}
    for line in files["SHA256SUMS"].decode().splitlines():
        digest, name = line.split("  ", 1)
        checksums[name.removeprefix("./")] = digest
    test.assertEqual(set(checksums), set(files) - {"SHA256SUMS"})
    for name, digest in checksums.items():
        test.assertEqual(hashlib.sha256(files[name]).hexdigest(), digest, name)
    # Every packaged playbook/role include must resolve inside the package.
    def inspect(node):
        if isinstance(node, list):
            for value in node:
                inspect(value)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in ("ansible.builtin.include_role", "ansible.builtin.import_role"):
                    role = value["name"]
                    test.assertIn(f"ansible/roles/{role}/tasks/main.yml", files)
                if key == "roles":
                    for role in value:
                        role = role if isinstance(role, str) else role.get("role", role.get("name"))
                        test.assertIn(f"ansible/roles/{role}/tasks/main.yml", files)
                inspect(value)
    for name, contents in files.items():
        if name.startswith("ansible/") and name.endswith(".yml"):
            inspect(yaml.safe_load(contents))
    # Copy complete roles and task includes, including nested templates/files.
    for name, contents in files.items():
        if name.startswith("ansible/roles/") and name.endswith("/tasks/main.yml"):
            role_dir = ROOT / str(Path(name).parents[1])
            for source in role_dir.rglob("*"):
                if source.is_file():
                    relative = str(source.relative_to(ROOT))
                    test.assertEqual(files.get(relative), source.read_bytes(), relative)
    for name in ("app", "keycloak", "postgres", "config", "shared-proxy"):
        from tests.runtime_fixture import RUNTIME
        test.assertEqual(files[f"kube/runtime/{name}.yaml"], (RUNTIME / f"{name}.yaml").read_bytes())
    guide = files["kube/runtime/README.md"].decode()
    results = files["kube/runtime/RESULTS.md"].decode()
    for pod in ("todo-app", "todo-keycloak", "todo-postgres", "shared-proxy"):
        test.assertIn(f"`{pod}`", guide)
        test.assertIn(f"`{pod}.service`", guide)
        test.assertIn(f"`{pod}`", results)
    test.assertIn("`nginx`", guide)
    test.assertIn("requires its own full unchanged-revision VM acceptance", results)
    return files



class OperationsDistributionTests(unittest.TestCase):
    def test_archive_contains_active_operations_without_transition_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "operations.tar.gz"
            subprocess.run(
                ["bash", str(ROOT / "scripts/build-operations-package.sh"), str(archive)],
                check=True,
                capture_output=True,
                text=True,
            )
            files = verify_package(self, archive, "todo-operations")
            for source in (ROOT / "ansible").glob("*.yml"):
                if source.name not in ("deploy.yml", "uninstall.yml"):
                    self.assertEqual(files.get("ansible/" + source.name), source.read_bytes())
            for source in (ROOT / "ansible/tasks").rglob("*"):
                if source.is_file():
                    self.assertEqual(files.get(str(source.relative_to(ROOT))), source.read_bytes())

            with tarfile.open(archive) as package:
                names = {name.removeprefix("todo-operations/") for name in package.getnames()}
            for path in (
                "ansible/bootstrap-standby.yml",
                "ansible/roles/shared_proxy_runtime/tasks/main.yml",
                "ansible/roles/shared_proxy_runtime/templates/shared-proxy.kube.j2",
                "kube/runtime/shared-proxy.yaml",
                "quadlet/todo-nginx-data.volume",
                "ansible/rebuild-standby.yml",
                "ansible/install-quarantine-tool.yml",
                "ansible/cluster-status.yml",
                "scripts/todo_dr.py",
                "scripts/todo_backup.py",
                "scripts/todo-quarantine.sh",
                "kube/runtime/app.yaml",
                "kube/runtime/postgres.yaml",
                "docs/ACCEPTANCE.md",
                "docs/ACCEPTANCE-TROUBLESHOOTING.md",
                "docs/ARCHITECTURE.md",
                "ansible/roles/postgres_reseed_standby/tasks/main.yml",
                "ansible/roles/todo_fapolicyd/tasks/main.yml",
            ):
                self.assertIn(path, names)
            for retired in (
                "scripts/manual_dr_commands.py",
                "scripts/lab_dr_acceptance.py",
                "scripts/todo_dr_run.py",
                "ansible/DR-AUTOMATION.md",
                "lab-dr.example.toml",
                "docs/MANUAL-DR-QUICKSTART.md",
                "docs/LAB-ACCEPTANCE.md",
                "ansible/roles/postgres_redundancy_primary/templates/todo-current-primary-entrypoint.sh.j2",
                "ansible/roles/postgres_reseed_standby/templates/todo-standby-entrypoint.sh.j2",
                "ansible/roles/postgres_standby/templates/todo-standby-entrypoint.sh.j2",
                "ansible/roles/promoted_application/templates/nginx.conf.j2",
                "ansible/roles/promoted_application/templates/todo-nginx-data.volume.j2",
            ):
                self.assertNotIn(retired, names)
            for name in names:
                self.assertNotIn("docs/legacy", name)
                self.assertNotIn("KUBE-MIGRATION.md", name)
                self.assertFalse(name.startswith("ansible/migrate-"))
                self.assertFalse(name.startswith("ansible/rollback-"))
                self.assertNotIn("docs/history", name)
                self.assertFalse(name.endswith(".container"))
                self.assertFalse(name.endswith(".container.j2"))
                self.assertFalse(name.startswith("ansible/roles/kube_application_"))
                self.assertFalse(name.startswith("ansible/roles/kube_postgres_primary_"))
            unpacked = Path(directory) / "isolated"
            unpacked.mkdir()
            for name, contents in files.items():
                target = unpacked / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(contents)
            # Execute only the real manifest-copy task, never bootstrap/reseed tasks.
            # Resolve defaults with each packaged caller's real play vars; syntax
            # checking alone cannot detect missing controller-side source paths.
            role = unpacked / "ansible/roles/postgres_kube_runtime"
            defaults = yaml.safe_load((role / "defaults/main.yml").read_text())
            tasks = yaml.safe_load((role / "tasks/main.yml").read_text())
            copy_task = next(task for task in tasks if
                             task.get("ansible.builtin.copy", {}).get("src") ==
                             "{{ todo_rendered_manifest_directory }}/{{ item }}")
            probes = []
            destinations = []
            for filename in ("bootstrap-standby.yml", "rebuild-standby.yml"):
                plays = yaml.safe_load((unpacked / "ansible" / filename).read_text())
                for play in plays:
                    if "roles" not in play:
                        continue
                    destination = unpacked / ("probe-" + str(len(probes)))
                    destination.mkdir()
                    destinations.append(destination)
                    probes.append({
                        "name": play["name"], "hosts": "localhost", "gather_facts": False,
                        "vars": {**defaults, **play["vars"],
                                 "todo_kube_runtime_directory": str(destination)},
                        "tasks": [copy_task],
                    })
            self.assertEqual(len(probes), 4)
            probe = unpacked / "ansible/manifest-path-check.yml"
            probe.write_text(yaml.safe_dump(probes))
            result = subprocess.run(
                [os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook"),
                 "-i", "localhost,", "-c", "local", str(probe)],
                cwd=unpacked, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for destination in destinations:
                for manifest in ("postgres.yaml", "config.yaml"):
                    self.assertEqual((destination / manifest).read_bytes(),
                                     files["kube/runtime/" + manifest])
            # Static import forces Ansible to resolve the normally dynamic proxy role.
            (unpacked / "ansible/proxy-check.yml").write_text(
                "- hosts: localhost\n  gather_facts: false\n  roles: [shared_proxy_runtime]\n")
            for playbook in ("deploy-promoted-application.yml", "proxy-check.yml"):
                subprocess.run(
                    [os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook"),
                     "-i", "ansible/inventory-recovery.example.ini",
                     "ansible/" + playbook, "--syntax-check"],
                    cwd=unpacked, check=True, capture_output=True, text=True,
                    env={**os.environ, "ANSIBLE_ROLES_PATH": str(unpacked / "ansible/roles")},
                )

    def test_offline_archive_contains_both_charts_and_all_image_slots(self):
        # Exercise the real packager; only expensive image production is substituted.
        # Real OCI build/load validation remains a separate release gate.
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            stub = directory / "podman"
            stub.write_text(
                f"#!{sys.executable}\n"
                "import pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "if args[0] == 'save':\n"
                "    pathlib.Path(args[args.index('--output') + 1]).write_text(args[-1])\n"
                "elif args[0] not in ('build', 'pull'):\n"
                "    raise SystemExit(99)\n"
            )
            stub.chmod(0o755)
            archive = directory / "offline.tar.gz"
            subprocess.run(["bash", str(ROOT / "offline/build-bundle.sh"), str(archive)],
                           check=True, capture_output=True,
                           env={**os.environ, "PATH": str(directory) + ":" + os.environ["PATH"]})
            files = verify_package(self, archive, "todo-offline-m12")
            for chart in ("todo", "shared-proxy"):
                for source in (ROOT / "helm" / chart).rglob("*"):
                    if source.is_file():
                        name = str(source.relative_to(ROOT))
                        self.assertEqual(files.get(name), source.read_bytes(), name)
            for image in ("todo-backend-m12", "todo-frontend-m12", "todo-proxy-m12",
                          "todo-keycloak-m12", "postgres-17.11"):
                self.assertIn(f"images/{image}.tar", files)
            self.assertIn("ansible/roles/todo_kube_runtime/templates/shared-proxy.kube.j2", files)
            self.assertIn("quadlet/todo-nginx-data.volume", files)
            self.assertNotIn("docs/legacy", "\n".join(files))
