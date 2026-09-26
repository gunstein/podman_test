"""Exercise the lab Proxmox client against an in-memory fake API."""
import contextlib
import io
import json
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/scripts"))

import pve_lab  # noqa: E402

CONFIG = {"PVE_HOST": "pve.lab", "PVE_NODE": "node1", "PVE_TOKEN_ID": "acceptance@pve!agent",
          "PVE_TOKEN_SECRET": "s3cret-token-value", "PVE_CA": "/dev/null"}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeProxmox:
    def __init__(self):
        self.requests = []
        self.config = {"net0": "virtio=BC:24:11:00:00:01,bridge=vmbr0,firewall=1",
                       "net1": "virtio=BC:24:11:00:00:02,bridge=vmbr1,link_down=0", "onboot": 1}
        self.exec_polls = 0
        self.task_polls = 0
        self.status = "running"
        self.ha = []

    def __call__(self, request, timeout):
        path = urllib.parse.urlsplit(request.full_url).path.removeprefix("/api2/json")
        query = urllib.parse.urlsplit(request.full_url).query
        body = request.data.decode() if request.data else ""
        self.requests.append((request.get_method(), path, query, body, dict(request.header_items())))
        data = None
        if path.endswith("/config") and request.get_method() == "GET":
            data = dict(self.config)
        elif path.endswith("/config") and request.get_method() == "PUT":
            self.config.update(dict(urllib.parse.parse_qsl(body)))
        elif path.endswith("/agent/exec"):
            data = {"pid": 42}
        elif path.endswith("/agent/exec-status"):
            self.exec_polls += 1
            data = {"exited": 1, "exitcode": 0, "out-data": "STOPPED\n"} if self.exec_polls > 1 else {"exited": 0}
        elif "/tasks/" in path:
            self.task_polls += 1
            data = {"status": "stopped", "exitstatus": "OK"} if self.task_polls > 1 else {"status": "running"}
        elif path.endswith("/rollback"):
            data = "UPID:node1:0001:0002:0003:qmrollback:107:acceptance@pve!agent:"
        elif path == "/cluster/ha/resources":
            data = self.ha
        elif path.endswith("/status/current"):
            data = {"status": self.status}
        elif path.endswith("/status/stop"):
            self.status = "stopped"
            data = "UPID:node1:0001:0002:0003:qmstop:107:acceptance@pve!agent:"
        return Response(json.dumps({"data": data}).encode())


class PveLabTests(unittest.TestCase):
    def run_main(self, argv, fake):
        client = pve_lab.Client(CONFIG, opener=fake, sleep=lambda seconds: None)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = pve_lab.main(argv, client=client)
        self.assertNotIn(CONFIG["PVE_TOKEN_SECRET"], stdout.getvalue() + stderr.getvalue())
        return code, stdout.getvalue(), stderr.getvalue()

    def test_token_is_sent_only_as_authorization_header(self):
        fake = FakeProxmox()
        code, _, _ = self.run_main(["get", "/nodes/{node}/qemu/107/config"], fake)
        self.assertEqual(code, 0)
        method, path, _, body, headers = fake.requests[0]
        self.assertEqual((method, path, body), ("GET", "/nodes/node1/qemu/107/config", ""))
        self.assertEqual(headers["Authorization"], "PVEAPIToken=acceptance@pve!agent=s3cret-token-value")

    def test_nic_flag_preserves_every_other_device_option(self):
        fake = FakeProxmox()
        code, out, _ = self.run_main(["nic", "107", "link_down", "1"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.config["net0"], "virtio=BC:24:11:00:00:01,bridge=vmbr0,firewall=1,link_down=1")
        self.assertEqual(fake.config["net1"], "virtio=BC:24:11:00:00:02,bridge=vmbr1,link_down=1")
        self.assertEqual(fake.config["onboot"], 1)
        self.assertIn("link_down=1", out)

    def test_guest_exec_waits_for_completion_and_returns_exit_code(self):
        fake = FakeProxmox()
        code, out, _ = self.run_main(
            ["exec", "107", "--", "/opt/todo/bin/app-quarantine.sh", "stop", "todo-primary", "gunstein"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(fake.requests[0][3])["command"],
                         ["/opt/todo/bin/app-quarantine.sh", "stop", "todo-primary", "gunstein"])
        self.assertEqual(fake.exec_polls, 2)
        self.assertIn("STOPPED", out)

    def test_task_waits_for_ok_exit_status(self):
        fake = FakeProxmox()
        code, _, _ = self.run_main(["task", "/nodes/{node}/qemu/107/snapshot/clean/rollback"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.task_polls, 2)
        self.assertTrue(fake.requests[-1][1].startswith("/nodes/node1/tasks/UPID%3Anode1%3A"))

    def test_failed_task_is_an_error(self):
        def failing(request, timeout):
            return Response(json.dumps({"data": {"status": "stopped", "exitstatus": "error"}}).encode())
        client = pve_lab.Client(CONFIG, opener=failing, sleep=lambda seconds: None)
        with self.assertRaisesRegex(pve_lab.LabError, "failed: error"):
            client.wait_task("UPID:node1:x")

    def test_fence_stops_the_vm_and_keeps_it_down(self):
        fake = FakeProxmox()
        code, out, _ = self.run_main(["fence", "107"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.requests[0][:2], ("GET", "/cluster/ha/resources"))
        self.assertIn(("POST", "/nodes/node1/qemu/107/status/stop"), [r[:2] for r in fake.requests])
        self.assertEqual((fake.status, fake.config["onboot"]), ("stopped", "0"))
        evidence = json.loads(out)
        self.assertEqual((evidence["status"], evidence["onboot"], evidence["ha"]),
                         ("stopped", "0", "not managed"))
        self.assertEqual(evidence["net0"], "virtio=BC:24:11:00:00:01,bridge=vmbr0,firewall=1,link_down=1")
        self.assertEqual(evidence["net1"], "virtio=BC:24:11:00:00:02,bridge=vmbr1,link_down=1")

    def test_fence_of_a_stopped_vm_skips_the_stop_and_can_be_repeated(self):
        fake = FakeProxmox()
        fake.status = "stopped"
        for _ in range(2):
            code, _, _ = self.run_main(["fence", "107"], fake)
            self.assertEqual(code, 0)
        self.assertNotIn("/nodes/node1/qemu/107/status/stop", [r[1] for r in fake.requests])

    def test_fence_refuses_an_ha_managed_vm_before_changing_anything(self):
        fake = FakeProxmox()
        fake.ha = [{"sid": "vm:107", "state": "started"}]
        code, _, err = self.run_main(["fence", "107"], fake)
        self.assertEqual(code, 1)
        self.assertIn("HA manages vm:107", err)
        self.assertEqual([r[0] for r in fake.requests], ["GET"])
        self.assertEqual((fake.status, fake.config["onboot"]), ("running", 1))

    def test_fence_fails_when_the_vm_does_not_stay_stopped(self):
        fake = FakeProxmox()
        original = fake.__call__

        def restarting(request, timeout):
            response = original(request, timeout)
            if request.full_url.endswith("/config") and request.get_method() == "PUT":
                fake.status = "running"
            return response
        code, _, err = self.run_main(["fence", "107"], restarting)
        self.assertEqual(code, 1)
        self.assertIn("not fenced: status=running", err)

    def test_config_file_requires_every_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pve.env"
            path.write_text("PVE_HOST=pve.lab\nPVE_TOKEN_SECRET='abc'\n")
            with self.assertRaisesRegex(pve_lab.LabError, "PVE_NODE"):
                pve_lab.load_config({"PVE_ENV": str(path)})


class PortsClosedTests(unittest.TestCase):
    SCRIPT = ROOT / "deploy/scripts/ports-closed.sh"

    def run_script(self, *arguments):
        return subprocess.run(["bash", str(self.SCRIPT), *arguments], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "PORT_TIMEOUT": "2"})

    def free_port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return str(probe.getsockname()[1])

    def test_refused_ports_are_closed(self):
        result = self.run_script("127.0.0.1", self.free_port(), self.free_port())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("refused", result.stdout)
        self.assertIn("CLOSED: 127.0.0.1", result.stdout)

    def test_an_open_port_fails(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = str(listener.getsockname()[1])
            result = self.run_script("127.0.0.1", self.free_port(), port)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"127.0.0.1:{port} open", result.stdout)
        self.assertIn("OPEN PORTS on 127.0.0.1", result.stdout)

    def test_usage_needs_a_host_and_a_port(self):
        self.assertEqual(self.run_script("127.0.0.1").returncode, 2)


if __name__ == "__main__":
    unittest.main()
