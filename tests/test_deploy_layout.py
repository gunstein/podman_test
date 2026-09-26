"""Exercise environment rendering and inventory resolution after source relocation."""
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class DeployLayoutTests(unittest.TestCase):
    def test_renderer_works_outside_checkout_and_applies_shared_environment(self):
        for environment, hostname in (("local", "todo.test"), ("prod", "todo.test")):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "rendered"
                subprocess.run([
                    str(ROOT / "deploy/scripts/render-kube-runtime.sh"),
                    str(ROOT / f"deploy/environments/{environment}/values.yaml"), str(output),
                ], cwd=directory, check=True, capture_output=True)
                self.assertEqual({p.name for p in output.iterdir()}, {
                    "app.yaml", "keycloak.yaml", "postgres.yaml", "config.yaml", "shared-proxy.yaml",
                    "notes-app.yaml", "notes-postgres.yaml", "notes-config.yaml",
                    "keycloak-postgres.yaml", "keycloak-config.yaml",
                })
                config = list(yaml.safe_load_all((output / "config.yaml").read_text()))
                proxy = list(yaml.safe_load_all((output / "shared-proxy.yaml").read_text()))
                proxy_env = next(d for d in proxy if d["metadata"]["name"] == "shared-nginx-env")
                self.assertEqual(proxy_env["data"]["TODO_TLS_HOSTNAME"], hostname)
                self.assertIn(f"https://{hostname}:8443", str(config))
                pods = [d for p in output.glob("*.yaml") for d in yaml.safe_load_all(p.read_text())
                        if d["kind"] == "Pod"]
                self.assertEqual({d["metadata"]["name"] for d in pods}, {
                    "todo-app", "todo-postgres", "notes-app", "notes-postgres", "keycloak",
                    "keycloak-postgres", "shared-proxy",
                })
