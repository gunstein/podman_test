"""Check the actual operations archive, not only its builder's source."""

import hashlib
import json
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
                    test.assertIn(f"deploy/ansible/roles/{role}/tasks/main.yml", files)
                if key == "roles":
                    for role in value:
                        role = role if isinstance(role, str) else role.get("role", role.get("name"))
                        test.assertIn(f"deploy/ansible/roles/{role}/tasks/main.yml", files)
                inspect(value)
    for name, contents in files.items():
        if name.startswith("deploy/ansible/") and name.endswith(".yml"):
            inspect(yaml.safe_load(contents))
    # Copy complete roles and task includes, including nested templates/files.
    for name, contents in files.items():
        if name.startswith("deploy/ansible/roles/") and name.endswith("/tasks/main.yml"):
            role_dir = ROOT / str(Path(name).parents[1])
            for source in role_dir.rglob("*"):
                if source.is_file():
                    relative = str(source.relative_to(ROOT))
                    test.assertEqual(files.get(relative), source.read_bytes(), relative)
    for name in ("app", "keycloak", "postgres", "config", "shared-proxy"):
        from tests.runtime_fixture import RUNTIME
        test.assertEqual(files[f"generated/kube-runtime/{name}.yaml"], (RUNTIME / f"{name}.yaml").read_bytes())
    guide = files["deploy/runtime/README.md"].decode()
    results = files["deploy/runtime/RESULTS.md"].decode()
    for pod in ("todo-app", "keycloak", "todo-postgres", "shared-proxy"):
        test.assertIn(f"`{pod}`", guide)
        test.assertIn(f"`{pod}.service`", guide)
        test.assertIn(f"`{pod}`", results)
    test.assertIn("`nginx`", guide)
    test.assertIn("requires its own full unchanged-revision VM acceptance", results)
    # Execute the packaged Python renderer outside the checkout. Neither imports
    # nor template paths may accidentally resolve back to the source tree.
    with tempfile.TemporaryDirectory() as directory:
        package_root = Path(directory)
        for name, contents in files.items():
            if name.startswith(("deploy/quadlet/", "deploy/installer/")):
                target = package_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(contents)
        for source in (ROOT / "deploy/installer/app_installer").glob("*.py"):
            relative = str(source.relative_to(ROOT))
            test.assertEqual(files.get(relative), source.read_bytes(), relative)
        result = subprocess.run([sys.executable, "-c", """
from pathlib import Path
from app_installer.quadlet import render
root = Path.cwd()
for name in ('todo-app', 'notes-app', 'keycloak', 'todo-postgres', 'notes-postgres', 'shared-proxy'):
    (root / (name + '.kube')).write_bytes(render(root, name + '.kube', {
        'todo_publish_address': '192.0.2.10', 'todo_service_port': 8443,
        'app_services': ['todo-app.service', 'notes-app.service'],
    }))
"""], cwd=package_root, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(package_root / "deploy/installer")})
        test.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for unit in package_root.glob("*.kube"):
            test.assertEqual(unit.read_bytes(), (RUNTIME / unit.name).read_bytes())
    return files



class OperationsDistributionTests(unittest.TestCase):
    def test_archive_contains_active_operations_without_transition_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "operations.tar.gz"
            subprocess.run(
                ["bash", str(ROOT / "deploy/scripts/build-operations-package.sh"), str(archive)],
                check=True,
                capture_output=True,
                text=True,
            )
            files = verify_package(self, archive, "todo-operations")
            for source in (ROOT / "deploy/ansible/playbooks").glob("*.yml"):
                if source.name not in ("deploy.yml", "uninstall.yml"):
                    self.assertEqual(files.get("deploy/ansible/playbooks/" + source.name), source.read_bytes())
            for source in (ROOT / "deploy/ansible/tasks").rglob("*"):
                if source.is_file():
                    self.assertEqual(files.get(str(source.relative_to(ROOT))), source.read_bytes())

            with tarfile.open(archive) as package:
                names = {name.removeprefix("todo-operations/") for name in package.getnames()}
            for path in (
                "deploy/ansible/playbooks/bootstrap-standby.yml",
                "deploy/installer/app_installer/workloads.py",
                "deploy/quadlet/shared-proxy.kube.j2",
                "generated/kube-runtime/shared-proxy.yaml",
                "deploy/ansible/playbooks/rebuild-standby.yml",
                "deploy/ansible/playbooks/install-quarantine-tool.yml",
                "deploy/ansible/playbooks/cluster-status.yml",
                "deploy/scripts/todo_dr.py",
                "deploy/scripts/todo_backup.py",
                "deploy/scripts/todo-quarantine.sh",
                "deploy/scripts/trust-files.sh",
                "generated/kube-runtime/app.yaml",
                "generated/kube-runtime/postgres.yaml",
                "docs/ACCEPTANCE.md",
                "docs/ACCEPTANCE-TROUBLESHOOTING.md",
                "docs/ARCHITECTURE.md",
                "deploy/ansible/roles/postgres_reseed_standby/tasks/main.yml",
                "deploy/ansible/roles/todo_fapolicyd/tasks/main.yml",
            ):
                self.assertIn(path, names)
            for retired in (
                "deploy/scripts/manual_dr_commands.py",
                "deploy/scripts/lab_dr_acceptance.py",
                "deploy/scripts/todo_dr_run.py",
                "deploy/ansible/DR-AUTOMATION.md",
                "lab-dr.example.toml",
                "docs/MANUAL-DR-QUICKSTART.md",
                "docs/LAB-ACCEPTANCE.md",
                "deploy/ansible/roles/postgres_redundancy_primary/templates/todo-current-primary-entrypoint.sh.j2",
                "deploy/ansible/roles/postgres_reseed_standby/templates/todo-standby-entrypoint.sh.j2",
                "deploy/ansible/roles/postgres_standby/templates/todo-standby-entrypoint.sh.j2",
                "deploy/ansible/roles/promoted_application/templates/nginx.conf.j2",
                "deploy/ansible/roles/promoted_application/templates/todo-nginx-data.volume.j2",
            ):
                self.assertNotIn(retired, names)
            self.assertFalse(any(name.endswith((".volume", ".volume.j2")) for name in names))
            for name in names:
                self.assertNotIn("docs/legacy", name)
                self.assertNotIn("KUBE-MIGRATION.md", name)
                self.assertFalse(name.startswith("deploy/ansible/migrate-"))
                self.assertFalse(name.startswith("deploy/ansible/rollback-"))
                self.assertNotIn("docs/history", name)
                self.assertFalse(name.endswith(".container"))
                self.assertFalse(name.endswith(".container.j2"))
                self.assertFalse(name.startswith("deploy/ansible/roles/kube_application_"))
                self.assertFalse(name.startswith("deploy/ansible/roles/kube_postgres_primary_"))
            unpacked = Path(directory) / "isolated"
            unpacked.mkdir()
            for name, contents in files.items():
                target = unpacked / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(contents)
            # Execute only the real manifest-copy task, never bootstrap/reseed tasks.
            # Resolve defaults with each packaged caller's real play vars; syntax
            # checking alone cannot detect missing controller-side source paths.
            tasks = yaml.safe_load((unpacked / "deploy/ansible/tasks/install-workload.yml").read_text())
            copy_task = next(task for task in tasks if task["name"] ==
                             "Stage the caller's rendered workload manifests on the target")
            metadata_result = subprocess.run(
                [sys.executable, '-m', 'app_installer', 'app-info'], cwd=unpacked,
                env={**os.environ, 'PYTHONPATH': str(unpacked / 'deploy/installer')},
                capture_output=True, text=True, check=True)
            metadata = json.loads(metadata_result.stdout)
            probes = []
            destinations = []
            for filename in ("bootstrap-standby.yml", "rebuild-standby.yml"):
                plays = yaml.safe_load((unpacked / "deploy/ansible/playbooks" / filename).read_text())
                for play in plays:
                    if "roles" not in play:
                        continue
                    destination = unpacked / ("probe-" + str(len(probes)))
                    (destination / "generated/kube-runtime").mkdir(parents=True)
                    destinations.append(destination / "generated/kube-runtime")
                    probes.append({
                        "name": play["name"], "hosts": "localhost", "gather_facts": False,
                        "vars": {**play["vars"], "app_installer_workload": "postgres",
                                 "app_installer_target": str(destination), "todo_app_metadata": metadata},
                        "tasks": [copy_task],
                    })
            self.assertEqual(len(probes), 4)
            probe = unpacked / "deploy/ansible/playbooks/manifest-path-check.yml"
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
                                     files["generated/kube-runtime/" + manifest])
            # Static import forces Ansible to parse the shared transport task.
            (unpacked / "deploy/ansible/playbooks/proxy-check.yml").write_text(
                "- hosts: localhost\n  gather_facts: false\n  tasks:\n"
                "    - ansible.builtin.import_tasks: ../tasks/install-workload.yml\n")
            for playbook in ("deploy-promoted-application.yml", "proxy-check.yml"):
                subprocess.run(
                    [os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook"),
                     "-i", "deploy/ansible/inventories/recovery/hosts.example.ini",
                     "deploy/ansible/playbooks/" + playbook, "--syntax-check"],
                    cwd=unpacked, check=True, capture_output=True, text=True,
                    env={key: value for key, value in os.environ.items()
                         if key not in ("ANSIBLE_ROLES_PATH", "ANSIBLE_CONFIG")},
                )

    def test_offline_archive_contains_quadlet_templates_and_all_image_slots(self):
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
                "elif args[:2] == ['image', 'exists']:\n"
                "    raise SystemExit(1)\n"
                "elif args[:2] == ['image', 'inspect']:\n"
                "    print('[{\"Labels\":{\"io.todo.proxy\":\"nginx\"}}]')\n"
                "elif args[0] not in ('build', 'pull'):\n"
                "    raise SystemExit(99)\n"
            )
            stub.chmod(0o755)
            archive = directory / "offline.tar.gz"
            subprocess.run(["bash", str(ROOT / "deploy/offline/build-bundle.sh"), str(archive)],
                           check=True, capture_output=True,
                           env={**os.environ, "PATH": str(directory) + ":" + os.environ["PATH"]})
            files = verify_package(self, archive, "todo-offline-m12")
            for source in (ROOT / "deploy/quadlet").glob("*.kube.j2"):
                name = str(source.relative_to(ROOT))
                self.assertEqual(files.get(name), source.read_bytes(), name)
            for image in ("todo-backend-m12", "todo-frontend-m12", "todo-proxy-m12",
                          "keycloak-m12", "postgres-17.11", "notes-backend-m12", "notes-frontend-m12"):
                self.assertIn(f"images/{image}.tar", files)
            self.assertIn("deploy/quadlet/shared-proxy.kube.j2", files)
            self.assertFalse(any(name.endswith((".volume", ".volume.j2")) for name in files))
            self.assertNotIn("docs/legacy", "\n".join(files))
