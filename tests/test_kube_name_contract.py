import pathlib
import unittest

import yaml

from tests.runtime_fixture import RUNTIME

ROOT = pathlib.Path(__file__).resolve().parents[1]


class KubeNameContractTests(unittest.TestCase):
    def test_current_workloads_preserve_operator_container_names(self):
        expected = {
            "todo-app": ("todo-app", {"todo-backend", "todo-frontend"}),
            "keycloak": ("keycloak", {"keycloak"}),
            "todo-postgres": ("todo-postgres", {"todo-postgres"}),
            "notes-app": ("notes-app", {"notes-backend", "notes-frontend"}),
            "notes-postgres": ("notes-postgres", {"notes-postgres"}),
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
        self.assertFalse(list((ROOT / "deploy/quadlet").glob("*.container")))
        self.assertFalse((ROOT / "deploy/ansible").exists())
        self.assertFalse((ROOT / "kube").exists())


class SharedResourceNameTests(unittest.TestCase):
    def test_the_shared_resources_keep_their_names(self):
        from app_installer import apps
        # nginx's unit reads the identity app's ConfigMap file.
        self.assertEqual(apps.PROXY_CONFIG_MANIFEST, apps.IDENTITY_APP.config_manifest)
        self.assertEqual(apps.PROXY_IMAGE, 'localhost/todo-proxy:m12')
        self.assertEqual(apps.PROXY_ARCHIVE, 'todo-proxy-m12.tar')
        self.assertEqual(apps.NGINX_TLS_VOLUME, 'todo-nginx-data')
        self.assertEqual(apps.PROXY_KUBE_TLS_SECRET, 'todo-kube-proxy-tls-secret')
        self.assertEqual(sorted(apps.PROXY_TLS_SECRETS.values()), [
            'todo-proxy-ca-cert', 'todo-proxy-ca-key', 'todo-proxy-tls-cert', 'todo-proxy-tls-incoming',
            'todo-proxy-tls-incoming-ca', 'todo-proxy-tls-key', 'todo-proxy-tls-mode',
            'todo-proxy-tls-request-key'])


if __name__ == "__main__":
    unittest.main()
