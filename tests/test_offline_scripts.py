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

    def test_a_target_hostname_is_passed_only_when_given(self):
        result = self.install("--target-external-hostname", "shop.example.org", "--publish-address", "192.168.0.102")
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(values[values.index("--target-external-hostname") + 1], "shop.example.org")
        self.assertEqual(values[values.index("--publish-address") + 1], "192.168.0.102")
        result = self.install("--publish-address", "192.168.0.102")
        self.assertNotIn("--target-external-hostname", json.loads(result.stdout.splitlines()[-1]))

    def test_each_apps_hostname_option_is_passed_on(self):
        result = self.install("--target-notes-hostname", "notes.example.org",
                              "--target-external-hostname", "shop.example.org")
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(values[values.index("--target-notes-hostname") + 1], "notes.example.org")
        self.assertEqual(values[values.index("--target-external-hostname") + 1], "shop.example.org")

    def test_invalid_arguments_never_start_installer(self):
        for arguments in (("--unknown",), ("--publish-address",), ("--target-external-hostname",),
                          ("--target-notes-hostname",),
                          ("--publish-address", "192.168.0.102", "--target-external-hostname"),
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
        names = ("todo-app.yaml", "keycloak.yaml", "todo-postgres.yaml", "todo-config.yaml", "shared-proxy.yaml",
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


class BuildBundleTests(unittest.TestCase):
    """build-bundle.sh as it runs, with only podman faked: the bundle it writes and its checksums."""

    def test_the_bundle_holds_the_target_files_and_bundle_json_under_its_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            fake = directory / "bin"
            fake.mkdir()
            (fake / "podman").write_text(
                "#!/bin/sh\n"
                "case \"$1 $2\" in\n"
                "  'image inspect') echo '[{\"Labels\":{\"io.todo.proxy\":\"nginx\"}}]' ;;\n"
                "  save*) while [ \"$#\" -gt 0 ]; do [ \"$1\" = --output ] && : > \"$2\"; shift; done ;;\n"
                "esac\n")
            (fake / "podman").chmod(0o755)
            output = directory / "dist/platform-offline-test.tar.gz"
            result = subprocess.run(
                ["bash", str(ROOT / "deploy/offline/build-bundle.sh"), str(output)],
                env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"},
                capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run(["tar", "-xzf", str(output), "-C", str(directory)], check=True)
            bundle = next(directory.glob("platform-offline-*/"))
            check = subprocess.run(["sha256sum", "--quiet", "-c", "SHA256SUMS"], cwd=bundle,
                                   capture_output=True, text=True, check=False)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            summed = {line.split("  ", 1)[1] for line in (bundle / "SHA256SUMS").read_text().splitlines()}
            metadata = json.loads((bundle / "bundle.json").read_text())
            expected = {"./bundle.json", "./VERSION", "./" + metadata["network"]}
            for key in ("manifests", "quadlets", "local_only_quadlets"):
                expected |= {f"./{metadata[key]['directory']}/{name}" for name in metadata[key]["files"]}
            self.assertLessEqual(expected, summed)
            self.assertIn("./deploy/installer/app_installer/target_render.py", summed)
            # The bundle's own installer reads its own metadata, as install.sh would.
            loaded = subprocess.run(
                [sys.executable, "-c", "import sys; from app_installer import target_render; "
                 "files = target_render.load(sys.argv[1], {'TARGET_PUBLISH_ADDRESS': '192.0.2.10'}, {}); "
                 "print(files.values['TARGET_EXTERNAL_HOSTNAME'], len(files.manifests), len(files.quadlets))",
                 str(bundle)],
                env={"PATH": os.environ["PATH"], "PYTHONPATH": str(bundle / "deploy/installer")},
                capture_output=True, text=True, check=False)
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(loaded.stdout.split(), ["todo.test", "10", "7"])
