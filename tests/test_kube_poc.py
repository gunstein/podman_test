import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
POC = ROOT / "kube" / "poc"


def read(name: str) -> str:
    return (POC / name).read_text(encoding="utf-8")


class KubePocTests(unittest.TestCase):
    def test_poc_is_isolated_from_the_accepted_runtime(self):
        tracked_runtime = {
            path.name
            for path in (ROOT / "quadlet").iterdir()
            if path.is_file()
        }
        poc_files = {path.name for path in POC.iterdir() if path.is_file()}

        self.assertTrue(tracked_runtime.isdisjoint(poc_files))
        self.assertTrue(
            all(
                "todo-kube-poc" in path.name
                or path.name
                in {
                    "README.md",
                    "RESULTS.md",
                    "server.yaml",
                    "consumer.yaml",
                }
                for path in POC.iterdir()
                if path.is_file()
            )
        )

    def test_independent_pods_use_explicit_restart_policy(self):
        server = read("server.yaml")
        consumer = read("consumer.yaml")

        self.assertIn("name: todo-kube-poc-server", server)
        self.assertIn("name: todo-kube-poc-consumer", consumer)
        self.assertIn("restartPolicy: Never", server)
        self.assertIn("restartPolicy: Never", consumer)
        self.assertIn("imagePullPolicy: Never", server)
        self.assertIn("imagePullPolicy: Never", consumer)
        self.assertIn("memory: 128Mi", server)
        self.assertIn("memory: 128Mi", consumer)
        self.assertNotIn("cpu:", server + consumer)
        self.assertIn("livenessProbe:\n        exec:", server)
        self.assertIn("socket.create_connection", server)
        self.assertIn("containerPort: 8000", server)
        self.assertNotIn("http.server", server)
        self.assertNotIn("tcpSocket:", server)
        for manifest in (server, consumer):
            self.assertIn("runAsUser: 1000", manifest)
            self.assertIn("runAsGroup: 1000", manifest)

    def test_external_secret_is_not_embedded(self):
        consumer = read("consumer.yaml")
        all_yaml = "\n".join(
            path.read_text(encoding="utf-8")
            for path in POC.glob("*.yaml")
        )
        yaml_fields = {line.strip() for line in all_yaml.splitlines()}

        self.assertIn("secretName: todo-kube-poc-secret", consumer)
        self.assertNotIn("kind: Secret", all_yaml)
        self.assertNotIn("stringData:", yaml_fields)
        self.assertNotIn("data:", yaml_fields)

    def test_kube_quadlets_use_same_yaml_and_report_failures(self):
        server_unit = read("todo-kube-poc-server.kube")
        consumer_unit = read("todo-kube-poc-consumer.kube")

        self.assertIn("Yaml=server.yaml", server_unit)
        self.assertIn("Yaml=consumer.yaml", consumer_unit)
        self.assertIn("Network=todo-kube-poc.network", server_unit)
        self.assertIn("Network=todo-kube-poc.network", consumer_unit)
        self.assertIn("ExitCodePropagation=any", server_unit)
        self.assertIn("ExitCodePropagation=any", consumer_unit)
        self.assertNotIn(
            "KubeDownForce=true", server_unit + consumer_unit
        )

    def test_persistent_volume_is_not_forced_down(self):
        consumer = read("consumer.yaml")
        readme = read("README.md")

        self.assertIn("persistentVolumeClaim:", consumer)
        self.assertIn("claimName: todo-kube-poc-data", consumer)
        self.assertIn("podman volume rm todo-kube-poc-data", readme)
        self.assertNotIn("podman kube play --down --force", readme)


if __name__ == "__main__":
    unittest.main()
