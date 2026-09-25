import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OfflineScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}

    def executable(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\nset -eu\n" + body)
        path.chmod(0o755)
        return str(path)

    def install(self, *arguments):
        bundle = self.directory / "bundle with spaces"
        bundle.mkdir(exist_ok=True)
        shutil.copy(ROOT / "deploy/offline/install.sh", bundle / "install.sh")
        (bundle / "preflight.sh").write_text("exit 0\n")
        self.executable("sha256sum", "exit 0\n")
        module = bundle / "deploy/installer/app_installer"
        module.mkdir(parents=True, exist_ok=True)
        (module / "__main__.py").write_text("import json,sys; print(json.dumps(sys.argv[1:]))")
        self.executable("python3", 'exec "' + sys.executable + '" "$@"\n')
        return subprocess.run(
            ["sh", str(bundle / "install.sh"), *arguments],
            env=self.env, capture_output=True, text=True, check=False,
        )

    def test_install_passes_explicit_address_to_python(self):
        result = self.install("--publish-address", "192.168.0.102")
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(values[values.index("--publish-address") + 1], "192.168.0.102")
        self.assertEqual(values[values.index("--deployment-mode") + 1], "offline")
        self.assertEqual(values[values.index("--bundle-dir") + 1], str(self.directory / "bundle with spaces"))

    def test_default_remains_loopback(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(values[values.index("--publish-address") + 1], "127.0.0.1")

    def test_invalid_arguments_never_start_installer(self):
        for arguments in (("--unknown",), ("--publish-address",),
                          ("--publish-address", "0.0.0.0"),
                          ("--publish-address", "::1"),
                          ("--publish-address", "192.168.0.102\nPublishPort=9999")):
            with self.subTest(arguments=arguments):
                result = self.install(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_render_failure_preserves_all_existing_manifests(self):
        output = self.directory / "output"
        output.mkdir()
        names = ("app.yaml", "keycloak.yaml", "postgres.yaml", "config.yaml", "shared-proxy.yaml",
                 "notes-app.yaml", "notes-postgres.yaml", "notes-config.yaml",
                 "keycloak-postgres.yaml", "keycloak-config.yaml")
        for name in names:
            (output / name).write_text(f"original {name}\n")
        project_root = self.directory / "project"
        shutil.copytree(ROOT / "deploy/manifests", project_root / "deploy/manifests")
        (project_root / "deploy/manifests/shared-proxy.yaml.j2").write_text("{% broken jinja syntax\n")
        result = subprocess.run(
            [sys.executable, "-m", "app_installer.render", str(project_root),
             str(ROOT / "deploy/environments/prod/values.yaml"), str(output)],
            env={**self.env, "PYTHONPATH": str(ROOT / "deploy/installer")},
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        for name in names:
            self.assertEqual((output / name).read_text(), f"original {name}\n")
