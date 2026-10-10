"""Exercise the actual embedded port checker without binding host sockets."""
import contextlib
import io
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = (ROOT / "deploy/offline/preflight.sh").read_text()
sys.path.insert(0, str(ROOT / "deploy/installer"))

from app_installer import apps  # noqa: E402

PORT_CHECK = PREFLIGHT.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


class PreflightHostPortTests(unittest.TestCase):
    def check_ports(self, busy, allowed=""):
        checked = []

        class FakeSocket:
            reuse = False

            def setsockopt(self, level, option, value):
                self.reuse = (level, option, value) == (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            def bind(self, address):
                # Only a listener counts, never a connection in TIME_WAIT: reuse is set first.
                if not self.reuse:
                    raise AssertionError("bind before SO_REUSEADDR")
                checked.append(address[1])
                if address[1] in busy:
                    raise OSError("Address already in use")

            def close(self):
                pass

        with patch.object(socket, "socket", FakeSocket), patch.dict(
            os.environ, {"PLATFORM_ALLOWED_PORTS": allowed}
        ), patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                contextlib.redirect_stderr(io.StringIO()):
            exec(compile(PORT_CHECK, "deploy/offline/preflight.sh:port-check", "exec"), {})
        return checked

    def test_host_backend_port_is_not_reserved(self):
        self.assertEqual(self.check_ports({8000}), [5432, 5433, 5434, 8080, 8443])
        self.assertNotIn('"todo-backend:8000"', PREFLIGHT)
        from tests.runtime_fixture import RUNTIME
        manifest = (RUNTIME / "todo-app.yaml").read_text()
        self.assertIn("containerPort: 8000", manifest)

    def test_unexpected_published_port_conflicts_are_rejected(self):
        for port in (5432, 5433, 5434, 8080, 8443):
            with self.subTest(port=port), self.assertRaisesRegex(SystemExit, str(port)):
                self.check_ports({port})

    def test_existing_allowed_ports_can_be_reused(self):
        self.assertEqual(
            self.check_ports({5432, 5433, 5434, 8080, 8443}, "5432,5433,5434,8080,8443"),
            [5432, 5433, 5434, 8080, 8443],
        )

    def test_every_registered_database_port_is_checked(self):
        checked = self.check_ports(set())
        for database in apps.registry().replicated_databases:
            self.assertIn(database.replication_port, checked)
            self.assertIn(f'"{database.container}:{database.replication_port}"', PREFLIGHT)

    def test_allowing_one_port_does_not_allow_another(self):
        with self.assertRaisesRegex(SystemExit, "8443"):
            self.check_ports({8080, 8443}, "8080")


class PreflightTimeWaitTests(unittest.TestCase):
    def test_a_port_with_only_time_wait_connections_is_free(self):
        # A real TIME_WAIT: the server side closes first, as nginx does after a health check.
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            client = socket.create_connection(("127.0.0.1", port))
            accepted, _ = server.accept()
            accepted.close()
            client.close()
        with socket.socket() as plain:
            with self.assertRaises(OSError):
                plain.bind(("127.0.0.1", port))  # the check before this fix
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))  # the check now
