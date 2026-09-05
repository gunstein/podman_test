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
                check=True, capture_output=True, text=True,
            )
            with tarfile.open(archive) as package:
                names = {name.removeprefix("todo-operations/") for name in package.getnames()}
            for path in (
                "ansible/bootstrap-standby.yml", "ansible/rebuild-standby.yml",
                "ansible/install-quarantine-tool.yml", "ansible/cluster-status.yml",
                "scripts/todo_dr.py", "scripts/todo_dr_run.py",
                "scripts/todo_backup.py", "scripts/todo-quarantine.sh",
                "kube/runtime/app.yaml", "kube/runtime/postgres.yaml",
                "docs/MANUAL-DR-QUICKSTART.md", "docs/ARCHITECTURE.md",
                "ansible/roles/postgres_reseed_standby/tasks/main.yml",
                "ansible/roles/todo_fapolicyd/tasks/main.yml",
            ):
                self.assertIn(path, names)
            for name in names:
                self.assertNotIn("docs/legacy", name)
                self.assertNotIn("KUBE-MIGRATION.md", name)
                self.assertFalse(name.startswith("ansible/migrate-"))
                self.assertFalse(name.startswith("ansible/rollback-"))
                self.assertFalse(name.startswith("ansible/roles/kube_application_"))
                self.assertFalse(name.startswith("ansible/roles/kube_postgres_primary_"))
            self.assertTrue(Path(str(archive) + ".sha256").is_file())

    def test_offline_builder_does_not_ship_legacy_guides(self):
        builder = (ROOT / "offline/build-bundle.sh").read_text()
        self.assertNotIn("docs/legacy", builder)
        self.assertIn("docs/MANUAL-DR-QUICKSTART.md", builder)
