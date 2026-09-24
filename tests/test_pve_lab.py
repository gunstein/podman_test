"""Exercise the lab Proxmox client against an in-memory fake API."""
import contextlib
import io
import json
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
            ["exec", "107", "--", "/opt/todo/bin/todo-quarantine.sh", "stop", "todo-primary", "gunstein"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(fake.requests[0][3])["command"],
                         ["/opt/todo/bin/todo-quarantine.sh", "stop", "todo-primary", "gunstein"])
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

    def test_config_file_requires_every_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pve.env"
            path.write_text("PVE_HOST=pve.lab\nPVE_TOKEN_SECRET='abc'\n")
            with self.assertRaisesRegex(pve_lab.LabError, "PVE_NODE"):
                pve_lab.load_config({"PVE_ENV": str(path)})


if __name__ == "__main__":
    unittest.main()
