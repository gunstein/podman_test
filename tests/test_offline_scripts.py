import json
import os
import shutil
import subprocess
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
        shutil.copy(ROOT / "offline/install.sh", bundle / "install.sh")
        (bundle / "preflight.sh").write_text("exit 0\n")
        self.executable("sha256sum", "exit 0\n")
        self.executable("ansible-playbook", 'for arg do printf "%s\\n" "$arg"; done\n')
        return subprocess.run(
            ["sh", str(bundle / "install.sh"), *arguments],
            env=self.env, capture_output=True, text=True, check=False,
        )

    def test_install_passes_explicit_address_as_json(self):
        result = self.install("--publish-address", "192.168.0.102")
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(values["todo_publish_address"], "192.168.0.102")
        self.assertEqual(values["deployment_mode"], "offline")
        self.assertEqual(values["bundle_directory"], str(self.directory / "bundle with spaces"))

    def test_default_remains_loopback(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])["todo_publish_address"],
                         "127.0.0.1")

    def test_invalid_arguments_never_start_ansible(self):
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
        names = ("app", "keycloak", "postgres", "config")
        for name in names:
            (output / f"{name}.yaml").write_text(f"original {name}\n")
        failing_helm = self.executable(
            "helm-fails-later",
            'case "$*" in *templates/postgres.yaml*) exit 1;; esac\nprintf "new content\\n"\n',
        )
        for helm in (str(self.directory / "missing-helm"), failing_helm):
            with self.subTest(helm=helm):
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts/render-kube-runtime.sh"),
                     str(ROOT / "helm/todo/values-prod.yaml"), str(output)],
                    env={**self.env, "HELM": helm}, capture_output=True, check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                for name in names:
                    self.assertEqual((output / f"{name}.yaml").read_text(),
                                     f"original {name}\n")
