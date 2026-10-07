"""Check the actual operations archive, not only its builder's source."""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

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
            verify_package(self, archive, "todo-operations")
            with tarfile.open(archive) as package:
                names = {name.removeprefix("todo-operations/") for name in package.getnames()}
            for path in (
                "deploy/installer/app_installer/workloads.py",
                "deploy/quadlet/shared-proxy.kube.j2",
                "generated/kube-runtime/shared-proxy.yaml",
                "deploy/dr/scripts/app_dr.py",
                "deploy/dr/scripts/app_backup.py",
                "deploy/dr/scripts/app-quarantine.sh",
                "deploy/dr/systemd/todo-dr-check.service",
                "deploy/dr/systemd/todo-dr-check.timer",
                "deploy/dr/systemd/todo-backup.service",
                "deploy/dr/systemd/todo-backup.timer",
                "deploy/dr/systemd/todo-replication-tls.service",
                "deploy/dr/systemd/todo-replication-tls.timer",
                "deploy/scripts/trust-files.sh",
                "deploy/dr/README.md",
                "deploy/dr/app_ops/cli.py",
                "deploy/dr/app_ops/transport.py",
                "generated/kube-runtime/app.yaml",
                "generated/kube-runtime/postgres.yaml",
                "docs/ACCEPTANCE.md",
                "deploy/dr/PROMOTION.md",
                "docs/ACCEPTANCE-TROUBLESHOOTING.md",
                "docs/ARCHITECTURE.md",
                "docs/runbooks/README.md",
                "docs/runbooks/primary-lost.md",
            ):
                self.assertIn(path, names)
            # The DR tools install the same rendered target files as the offline bundle.
            self.assertIn("bundle.json", names)
            self.assertIn("generated/target/manifests/postgres.yaml", names)
            self.assertIn("generated/target/quadlet/replicated/todo-postgres.kube", names)
            with tarfile.open(archive) as package:
                package.extractall(directory, filter="data")
            sys.path.insert(0, str(ROOT / "deploy/installer"))
            from app_installer import target_render
            target = target_render.load(Path(directory) / "todo-operations",
                                        {target_render.PUBLISH_ADDRESS: "192.0.2.10"}, environment={}, recorded={})
            self.assertEqual(target.hostnames, {"todo": "todo.test", "notes": "notes.test"})
            self.assertIn(b"192.0.2.10:5432:5432", target.replicated["todo-postgres.kube"])
            for retired in (
                "deploy/scripts/manual_dr_commands.py",
                "deploy/scripts/lab_dr_acceptance.py",
                "deploy/scripts/todo_dr_run.py",
                "lab-dr.example.toml",
                "docs/MANUAL-DR-QUICKSTART.md",
                "docs/LAB-ACCEPTANCE.md",
            ):
                self.assertNotIn(retired, names)
            self.assertFalse(any(name.endswith((".volume", ".volume.j2")) for name in names))
            for name in names:
                self.assertNotIn("docs/legacy", name)
                self.assertNotIn("KUBE-MIGRATION.md", name)
                # Ansible is retired: app-ops over plain SSH replaces every playbook.
                self.assertFalse(name.startswith("deploy/ansible/"))
                self.assertNotEqual(name, "ansible.cfg")
                self.assertNotIn("docs/history", name)
                self.assertFalse(name.endswith(".container"))
                self.assertFalse(name.endswith(".container.j2"))

    def test_every_dr_entry_point_starts_from_the_package_layout(self):
        # Build the real package, unpack it outside the checkout, and lay it out
        # as it is used: app-ops runs from the package on the controller, and
        # on a host app-ops puts app_installer and app_dr_host side by side in
        # /opt/todo/lib (trust.stage_installer) and the tools in /opt/todo/bin.
        # Every entry point must start without reaching back into the checkout.
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            archive = directory / "operations.tar.gz"
            subprocess.run(["bash", str(ROOT / "deploy/scripts/build-operations-package.sh"), str(archive)],
                           check=True, capture_output=True)
            with tarfile.open(archive) as package:
                package.extractall(directory / "unpacked", filter="data")
            package = directory / "unpacked/todo-operations"
            host = directory / "opt/todo"
            for name, source in (("app_installer", "deploy/installer/app_installer"),
                                 ("app_dr_host", "deploy/dr/app_dr_host")):
                (host / "lib" / name).mkdir(parents=True)
                for module in (package / source).glob("*.py"):
                    shutil.copy(module, host / "lib" / name)
            (host / "bin").mkdir()
            for tool in ("app_dr.py", "app_backup.py"):
                shutil.copy(package / "deploy/dr/scripts" / tool, host / "bin")
            environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            for where, command, pythonpath in (
                    ("controller", ["-m", "app_ops", "--help"], package / "deploy/dr"),
                    ("host", ["-m", "app_dr_host", "--help"], host / "lib"),
                    ("host", [str(host / "bin/app_dr.py"), "--help"], None),
                    ("host", [str(host / "bin/app_backup.py"), "--help"], None)):
                with self.subTest(where=where, command=command[-2]):
                    env = {**environment, **({"PYTHONPATH": str(pythonpath)} if pythonpath else {})}
                    result = subprocess.run([sys.executable, *command], cwd=directory, env=env,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)

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
            from app_installer import settings
            files = verify_package(self, archive, f"todo-offline-{settings.IMAGE_TAG}")
            for source in (ROOT / "deploy/quadlet").glob("*.kube.j2"):
                name = str(source.relative_to(ROOT))
                self.assertEqual(files.get(name), source.read_bytes(), name)
            for image in ("todo-backend-m12", "todo-frontend-m12", "todo-proxy-m12",
                          "keycloak-m12", "postgres-17.11", "notes-backend-m12", "notes-frontend-m12"):
                self.assertIn(f"images/{image}.tar", files)
            self.assertIn("deploy/quadlet/shared-proxy.kube.j2", files)
            self.assertFalse(any(name.endswith((".volume", ".volume.j2")) for name in files))
            self.assertNotIn("docs/legacy", "\n".join(files))
