"""Check the actual operations archive, not only its builder's source."""

import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
            with tarfile.open(archive) as package:
                names = {name.removeprefix("todo-operations/") for name in package.getnames()}
            for path in (
                "ansible/bootstrap-standby.yml",
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
            self.assertTrue(Path(str(archive) + ".sha256").is_file())

    def test_offline_builder_does_not_ship_legacy_guides(self):
        builder = (ROOT / "offline/build-bundle.sh").read_text()
        self.assertNotIn("docs/legacy", builder)
        self.assertIn("docs/ACCEPTANCE.md", builder)
        self.assertIn("docs/ACCEPTANCE-TROUBLESHOOTING.md", builder)
