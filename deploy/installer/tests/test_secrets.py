import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, secrets  # noqa: E402
from fake_host import FakeHost  # noqa: E402

# base64 of "fixture-password", the value FakeHost gives every password secret.
FIXTURE = "Zml4dHVyZS1wYXNzd29yZA=="


class KubeSecretTests(unittest.TestCase):
    def test_kube_secrets_are_built_in_memory_from_the_podman_secrets(self):
        with FakeHost() as host:
            self.assertTrue(secrets.create_kube({**secrets.application_secret_mapping(apps.registry().apps[0]),
                                                 **secrets.keycloak_secret_mapping()}))
        created = [argv[3] for argv in host.ran("podman", "secret", "create")]
        self.assertEqual(set(created), {
            "todo-kube-migrator-secret", "todo-kube-backend-secret", "keycloak-kube-admin-secret"})
        for name in created:
            payload = json.loads(host.secrets[name])
            self.assertEqual(payload["kind"], "Secret")
            key = "bootstrap-admin-password" if name == "keycloak-kube-admin-secret" else "database-password"
            self.assertEqual(payload["data"][key], FIXTURE)

    def test_a_kube_secret_that_differs_from_its_podman_secret_is_refused(self):
        mapping = secrets.application_secret_mapping(apps.registry().apps[0])
        migrator, backend = mapping
        current = json.dumps({"kind": "Secret", "data": {"database-password": FIXTURE}})
        with FakeHost() as host:
            host.secrets[migrator] = json.dumps({"kind": "Secret", "data": {"database-password": "b2xkLXBhc3N3b3Jk"}})
            host.secrets[backend] = current
            with self.assertRaisesRegex(RuntimeError, migrator) as refused:
                secrets.create_kube(mapping)
            self.assertNotIn(backend, str(refused.exception))
            self.assertNotIn("fixture-password", str(refused.exception))
            host.secrets[migrator] = current
            self.assertFalse(secrets.create_kube(mapping))
        self.assertFalse(host.ran("podman", "secret", "create"))


if __name__ == "__main__":
    unittest.main()
