import pathlib
import unittest

import yaml

from tests.runtime_fixture import RUNTIME

ROOT = pathlib.Path(__file__).resolve().parents[1]


class KubeNameContractTests(unittest.TestCase):
    def test_current_workloads_preserve_operator_container_names(self):
        expected = {
            "app": ("todo-app", {"todo-backend", "todo-frontend"}),
            "keycloak": ("todo-keycloak", {"todo-keycloak"}),
            "postgres": ("todo-postgres", {"todo-postgres"}),
            "shared-proxy": ("shared-proxy", {"nginx"}),
        }
        for filename, (pod, containers) in expected.items():
            with self.subTest(pod=pod):
                docs = list(yaml.safe_load_all((RUNTIME / f"{filename}.yaml").read_text()))
                manifest = next(doc for doc in docs if doc["kind"] == "Pod")
                self.assertEqual(manifest["metadata"]["name"], pod)
                self.assertEqual(
                    {item["name"] for item in manifest["spec"]["containers"]}, containers
                )
                unit = (RUNTIME / f"{pod}.kube").read_text()
                self.assertIn("PodmanArgs=--no-pod-prefix", unit)
                self.assertIn("ExitCodePropagation=any", unit)
                self.assertIn("Restart=on-failure", unit)

    def test_legacy_runtime_and_transition_roles_stay_retired(self):
        self.assertFalse(list((ROOT / "quadlet").glob("*.container")))
        for name in (
            "kube_application_migration",
            "kube_application_rollback",
            "kube_postgres_primary_migration",
            "kube_postgres_primary_rollback",
        ):
            self.assertFalse((ROOT / "ansible" / "roles" / name).exists())
        self.assertEqual({p.name for p in (ROOT / "kube").iterdir() if p.is_dir()}, {"runtime"})


if __name__ == "__main__":
    unittest.main()
