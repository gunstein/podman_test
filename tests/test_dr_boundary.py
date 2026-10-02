"""The single-host installer and DR stay apart: DR may use the installer, never the other way."""
import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DR_PACKAGES = {"app_dr_host", "app_ops"}


def imported(path):
    """The top-level package names a Python file imports."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


class BoundaryTests(unittest.TestCase):
    def test_the_installer_imports_no_dr_package(self):
        for path in sorted((ROOT / "deploy/installer/app_installer").glob("*.py")):
            with self.subTest(path=path.name):
                self.assertFalse(imported(path) & DR_PACKAGES)

    def test_the_offline_bundle_carries_no_dr_code(self):
        builder = (ROOT / "deploy/offline/build-bundle.sh").read_text()
        self.assertNotIn("deploy/dr", builder)
        self.assertNotIn("app_ops", builder)

    def test_the_dr_modules_are_not_left_in_the_installer(self):
        installer = {path.name for path in (ROOT / "deploy/installer/app_installer").glob("*.py")}
        dr_host = {path.name for path in (ROOT / "deploy/dr/app_dr_host").glob("*.py")}
        self.assertFalse({"replication.py", "replication_tls.py", "promoted.py"} & installer)
        self.assertLessEqual({"replication.py", "replication_tls.py", "promoted.py", "cli.py"}, dr_host)

    def test_the_dr_tools_load_without_jinja2(self):
        # They install the package's pre-rendered target files, so a DR host needs no Jinja2.
        script = (
            "import sys\n"
            "class Blocked:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in ('jinja2', 'markupsafe'):\n"
            "            raise ImportError(name + ' is not installed on this host')\n"
            "sys.meta_path.insert(0, Blocked())\n"
            "import app_dr_host.cli, app_dr_host.promoted, app_dr_host.replication, app_ops.cli\n")
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env={
            **os.environ, "PYTHONPATH": f"{ROOT / 'deploy/installer'}:{ROOT / 'deploy/dr'}"})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
