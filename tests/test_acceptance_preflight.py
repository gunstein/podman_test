"""The laptop readiness check must stay read-only and report real gaps."""
import argparse
import contextlib
import io
import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/scripts"))

import acceptance_preflight  # noqa: E402
import pve_lab  # noqa: E402

REAL_CLIENT = pve_lab.Client
CONFIG = {"PVE_HOST": "pve.lab", "PVE_NODE": "node1", "PVE_TOKEN_ID": "acceptance@pve!agent",
          "PVE_TOKEN_SECRET": "s3cret-token-value", "PVE_CA": "/dev/null"}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_api(methods, privileges, sdn_privileges=("SDN.Use",), stale_rules=()):
    def opener(request, timeout):
        methods.append(request.get_method())
        url = urllib.parse.urlsplit(request.full_url)
        path = url.path.removeprefix("/api2/json")
        if path == "/version":
            data = {"version": "8.4.1"}
        elif path == "/access/permissions":
            target = urllib.parse.parse_qs(url.query)["path"][0]
            granted = sdn_privileges if target == "/sdn" else privileges
            data = {target: dict.fromkeys(granted, 1)}
        elif path.endswith("/firewall/rules"):
            data = list(stale_rules)
        elif path.endswith("/firewall/options") and "/qemu/" in path:
            data = {}
        elif path == "/nodes/node1/firewall/options":
            data = {"enable": 1}
        elif path.endswith("/config"):
            data = {"agent": "1", "onboot": 1, "net0": "virtio=BC:24:11:00:00:01,bridge=vmbr0"}
        elif path.endswith("/status/current"):
            data = {"status": "running"}
        elif path.endswith("/snapshot"):
            data = [{"name": "clean-agent"}, {"name": "current"}]
        elif path == "/cluster/firewall/options":
            data = {"enable": 1}
        elif path == "/cluster/ha/resources":
            data = []
        else:
            raise AssertionError(path)
        return Response(json.dumps({"data": data}).encode())
    return opener


class AcceptancePreflightTests(unittest.TestCase):
    def check(self, privileges, env_mode=0o600, sdn_privileges=("SDN.Use",), stale_rules=()):
        methods = []
        args = argparse.Namespace(
            primary_vmid="107", standby_vmid="108", snapshot="clean-agent")
        report = acceptance_preflight.Report()
        opener = fake_api(methods, privileges, sdn_privileges=sdn_privileges, stale_rules=stale_rules)
        with patch.object(pve_lab, "load_config", return_value=CONFIG), \
                patch.object(pve_lab, "Client", lambda config: REAL_CLIENT(config, opener=opener)), \
                patch.object(acceptance_preflight.Path, "is_file", return_value=True), \
                patch.object(acceptance_preflight.Path, "stat", return_value=type("S", (), {"st_mode": 0o100000 | env_mode})()):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                acceptance_preflight.check_proxmox(report, args)
        self.assertEqual(set(methods), {"GET"})
        self.assertNotIn(CONFIG["PVE_TOKEN_SECRET"], output.getvalue())
        return report, output.getvalue()

    def test_complete_lab_passes_using_only_get_requests(self):
        report, output = self.check(acceptance_preflight.REQUIRED_PRIVILEGES + ("VM.Monitor",))
        self.assertEqual(report.failed, 0, output)
        self.assertIn("snapshot 'clean-agent'", output)
        self.assertNotIn("disabled: the agent will ask", output)
        self.assertNotIn("enabled; phase 1", output)

    def test_missing_privileges_and_readable_token_file_fail(self):
        report, output = self.check(("VM.Audit",), env_mode=0o644)
        self.assertIn("missing VM.PowerMgmt", output)
        self.assertIn("FAIL  VM 107 Guest Agent exec privilege", output)
        self.assertIn("FAIL  Token file not readable by others", output)
        self.assertGreaterEqual(report.failed, 5)

    def test_missing_sdn_use_fails(self):
        report, output = self.check(acceptance_preflight.REQUIRED_PRIVILEGES + ("VM.Monitor",),
                                    sdn_privileges=())
        self.assertIn("FAIL  Token has SDN.Use on /sdn", output)
        self.assertIn("ACCEPTANCE-AGENT.md A1", output)
        self.assertGreaterEqual(report.failed, 1)

    def test_disabled_node_firewall_is_reported_separately_from_datacenter(self):
        methods = []
        args = argparse.Namespace(primary_vmid="107", standby_vmid="108", snapshot="clean-agent")
        report = acceptance_preflight.Report()
        opener = fake_api(methods, acceptance_preflight.REQUIRED_PRIVILEGES + ("VM.Monitor",))

        def node_firewall_off(request, timeout):
            path = urllib.parse.urlsplit(request.full_url).path.removeprefix("/api2/json")
            if path == "/nodes/node1/firewall/options":
                return Response(json.dumps({"data": {"enable": 0}}).encode())
            return opener(request, timeout)

        with patch.object(pve_lab, "load_config", return_value=CONFIG), \
                patch.object(pve_lab, "Client", lambda config: REAL_CLIENT(config, opener=node_firewall_off)), \
                patch.object(acceptance_preflight.Path, "is_file", return_value=True), \
                patch.object(acceptance_preflight.Path, "stat",
                            return_value=type("S", (), {"st_mode": 0o100600})()):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                acceptance_preflight.check_proxmox(report, args)
        output = output.getvalue()
        self.assertIn("FAIL  Node's own firewall enabled", output)
        self.assertIn("Datacenter firewall enabled", output)
        self.assertNotIn("FAIL  Datacenter firewall enabled", output)

    def test_leftover_firewall_rules_warn_with_their_position_and_comment(self):
        report, output = self.check(acceptance_preflight.REQUIRED_PRIVILEGES + ("VM.Monitor",),
                                    stale_rules=({"pos": 0, "comment": "todo-quarantine-replication"},))
        self.assertEqual(report.failed, 0, output)
        self.assertIn("WARN  VM 107 has no leftover firewall rules", output)
        self.assertIn("0:todo-quarantine-replication", output)

    def test_guest_checks_are_read_only_commands(self):
        for forbidden in ("rm ", "systemctl start", "systemctl stop", "firewall-cmd", "podman rm",
                          "podman volume rm", "tee ", ">"):
            self.assertNotIn(forbidden, acceptance_preflight.SSH_CHECKS.replace("2>/dev/null", ""))

    def check_guest(self, jinja2_yaml):
        facts = {"hostname": "todo-primary", "client": "192.168.0.100", "selinux": "Enforcing",
                "unit_sshd": "active", "unit_firewalld": "active", "unit_fapolicyd": "active",
                "unit_qemu-guest-agent": "active", "linger": "yes", "rootless": "true",
                "podman": "podman version 5.8.2", "jinja2_yaml": jinja2_yaml, "sudo": "ok",
                "sudoers_file": "present", "mem_mib": "3457", "home_free": "16G", "todo_state": "0"}
        out = "\n".join(f"{key}={value}" for key, value in facts.items())
        args = argparse.Namespace(client_ip="192.168.0.100", user="gunstein")
        report = acceptance_preflight.Report()
        with patch.object(acceptance_preflight, "_ssh", return_value=(0, out, "")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                acceptance_preflight.check_guest(report, args, "192.168.0.102", "todo-primary")
        return report, output.getvalue()

    def test_guest_reports_missing_jinja2_or_pyyaml(self):
        report, output = self.check_guest("missing")
        self.assertIn("FAIL  Python Jinja2 and PyYAML installed", output)
        self.assertIn("prepare-agent-snapshots.sh", output)
        self.assertGreaterEqual(report.failed, 1)

    def test_guest_passes_with_jinja2_and_pyyaml_present(self):
        report, output = self.check_guest("ok")
        self.assertIn("PASS  Python Jinja2 and PyYAML installed", output)
        self.assertEqual(report.failed, 0, output)


if __name__ == "__main__":
    unittest.main()
