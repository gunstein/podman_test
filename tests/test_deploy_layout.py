"""Exercise environment rendering and inventory resolution after source relocation."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/installer"))
from app_installer import platform_file, render  # noqa: E402


class DeployLayoutTests(unittest.TestCase):
    def test_renderer_works_outside_checkout_and_applies_shared_environment(self):
        for environment, hostname in (("local", "auth.test"), ("prod", "auth.test")):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "rendered"
                subprocess.run([
                    str(ROOT / "deploy/scripts/render-kube-runtime.sh"),
                    environment, str(output),
                ], cwd=directory, check=True, capture_output=True)
                platform, settings = platform_file.load(ROOT / 'platform.yaml', environment)
                expected = render.files(ROOT, platform, render.hostnames(platform), platform.identity_hostname,
                                        settings.public_port, settings.log_level)
                self.assertEqual({p.name for p in output.iterdir()}, set(expected))
                config = list(yaml.safe_load_all((output / "todo-config.yaml").read_text()))
                proxy = list(yaml.safe_load_all((output / "shared-proxy.yaml").read_text()))
                proxy_env = next(d for d in proxy if d["metadata"]["name"] == "shared-nginx-env")
                self.assertEqual(proxy_env["data"]["PLATFORM_TLS_HOSTNAME"], hostname)
                self.assertIn(f"https://{hostname}:8443", str(config))
                pods = [d for p in output.glob("*.yaml") for d in yaml.safe_load_all(p.read_text())
                        if d["kind"] == "Pod"]
                self.assertEqual({d["metadata"]["name"] for d in pods},
                                 {workload.pod for workload in platform.workloads()})
