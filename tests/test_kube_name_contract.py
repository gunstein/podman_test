import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "kube" / "name-contract"


class KubeNameContractTests(unittest.TestCase):
    def test_pod_and_container_share_the_stable_name(self):
        manifest = yaml.safe_load(
            (CONTRACT / "contract.yaml").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["metadata"]["name"], "todo-kube-name-contract")
        self.assertEqual(
            manifest["spec"]["containers"][0]["name"],
            "todo-kube-name-contract",
        )
        self.assertEqual(manifest["spec"]["restartPolicy"], "Never")
        self.assertEqual(
            manifest["spec"]["containers"][0]["command"], ["sleep"]
        )
        self.assertEqual(
            manifest["spec"]["containers"][0]["args"], ["infinity"]
        )

    def test_quadlet_disables_the_pod_name_prefix(self):
        unit = (CONTRACT / "todo-kube-name-contract.kube").read_text(
            encoding="utf-8"
        )

        self.assertIn("PodmanArgs=--no-pod-prefix", unit)
        self.assertIn("ExitCodePropagation=any", unit)
        self.assertIn(
            "ExecStartPost=/usr/bin/podman update "
            "--health-on-failure=kill todo-kube-name-contract",
            unit,
        )
        self.assertIn("Restart=on-failure", unit)

    def test_contract_has_no_persistent_or_network_side_effects(self):
        manifest = (CONTRACT / "contract.yaml").read_text(encoding="utf-8")
        unit = (CONTRACT / "todo-kube-name-contract.kube").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("PersistentVolumeClaim", manifest)
        self.assertNotIn("kind: Secret", manifest)
        self.assertIn("Network=none", unit)
        self.assertIn("/tmp/todo-kube-force-unhealthy", manifest)


if __name__ == "__main__":
    unittest.main()
