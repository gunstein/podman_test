from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ComposePrototypeTests(unittest.TestCase):
    def test_provider_is_selected_explicitly(self):
        script = (ROOT / "scripts/test-compose-provider.sh").read_text()

        self.assertIn("PODMAN_COMPOSE_PROVIDER=", script)
        self.assertIn("/usr/bin/podman-compose", script)
        self.assertIn("TODO_COMPOSE_REPOSITORY_DIRECTORY", script)
        self.assertNotIn("/usr/local/bin/docker-compose", script)

    def test_probe_uses_native_external_secret_and_rootless_volume(self):
        compose = (ROOT / "compose/prototype/compose.yaml").read_text()

        self.assertIn("external: true", compose)
        self.assertIn("todo-compose-probe-secret", compose)
        self.assertIn('user: "999:999"', compose)
        self.assertIn(":/probe:U,Z", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("cap_drop:", compose)
        self.assertNotIn("file:", compose)

    def test_probe_refuses_to_reuse_resources(self):
        script = (ROOT / "scripts/test-compose-provider.sh").read_text()

        self.assertIn("Refusing to reuse existing container", script)
        self.assertIn("Refusing to reuse existing secret", script)
        self.assertIn("Refusing to reuse existing volume", script)
        self.assertIn("Refusing to reuse existing network", script)
        self.assertIn("trap cleanup EXIT INT TERM", script)


if __name__ == "__main__":
    unittest.main()
