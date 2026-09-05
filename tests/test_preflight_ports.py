"""Exercise the actual embedded port checker without binding host sockets."""
import os
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = (ROOT / "offline/preflight.sh").read_text()
PORT_CHECK = PREFLIGHT.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


class PreflightHostPortTests(unittest.TestCase):
    def check_ports(self, busy, allowed=""):
        checked = []

        class FakeSocket:
            def bind(self, address):
                checked.append(address[1])
                if address[1] in busy:
                    raise OSError("Address already in use")

            def close(self):
                pass

        with patch.object(socket, "socket", FakeSocket), patch.dict(
            os.environ, {"TODO_ALLOWED_PORTS": allowed}
        ):
            exec(compile(PORT_CHECK, "offline/preflight.sh:port-check", "exec"), {})
        return checked

    def test_host_backend_port_is_not_reserved(self):
        self.assertEqual(self.check_ports({8000}), [5432, 8080, 8443])
        self.assertNotIn('"todo-backend:8000"', PREFLIGHT)
        manifest = (ROOT / "kube/runtime/app.yaml").read_text()
        self.assertIn("containerPort: 8000", manifest)

    def test_unexpected_published_port_conflicts_are_rejected(self):
        for port in (5432, 8080, 8443):
            with self.subTest(port=port), self.assertRaisesRegex(SystemExit, str(port)):
                self.check_ports({port})

    def test_existing_allowed_ports_can_be_reused(self):
        self.assertEqual(
            self.check_ports({5432, 8080, 8443}, "5432,8080,8443"),
            [5432, 8080, 8443],
        )

    def test_allowing_one_port_does_not_allow_another(self):
        with self.assertRaisesRegex(SystemExit, "8443"):
            self.check_ports({8080, 8443}, "8080")
