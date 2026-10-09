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
sys.path.insert(0, str(ROOT / "deploy/scripts/lab"))

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

    def check_guest(self, pyyaml, stderr="", synchronised="yes"):
        facts = {"hostname": "todo-primary", "client": "192.168.0.100", "selinux": "Enforcing",
                "unit_sshd": "active", "unit_firewalld": "active", "unit_fapolicyd": "active",
                "unit_qemu-guest-agent": "active", "linger": "yes", "rootless": "true",
                "podman": "podman version 5.8.2", "pyyaml": pyyaml, "sudo": "ok",
                "sudoers_file": "present", "mem_mib": "3457", "home_free": "16G", "todo_state": "0",
                "ntp": "yes", "ntp_synchronized": synchronised, "journal": "persistent"}
        out = "\n".join(f"{key}={value}" for key, value in facts.items())
        args = argparse.Namespace(client_ip="192.168.0.100", user="gunstein")
        report = acceptance_preflight.Report()
        with patch.object(acceptance_preflight, "_ssh", return_value=(0, out, stderr)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                acceptance_preflight.check_guest(report, args, "192.168.0.102", "todo-primary")
        return report, output.getvalue()

    def test_guest_reports_missing_pyyaml(self):
        report, output = self.check_guest("missing")
        self.assertIn("FAIL  Python PyYAML installed (DR tools)", output)
        self.assertIn("prepare-agent-snapshots.sh", output)
        self.assertGreaterEqual(report.failed, 1)

    def test_guest_passes_with_pyyaml_and_needs_no_jinja2(self):
        warning = "** The server may need to be upgraded. See https://openssh.com/pq.html"
        report, output = self.check_guest("ok", stderr=warning)
        # A passing line shows neither SSH's warning nor the advice for a missing PyYAML.
        self.assertIn("PASS  Key-based SSH with verified host key\n", output)
        self.assertIn("PASS  Python PyYAML installed (DR tools)\n", output)
        self.assertEqual(report.failed, 0, output)
        self.assertNotIn("jinja2", acceptance_preflight.SSH_CHECKS)


    def test_an_unsynchronised_clock_warns_and_says_what_to_check(self):
        report, output = self.check_guest("ok")
        self.assertIn("PASS  Clock synchronised (NTP)\n", output)
        report, output = self.check_guest("ok", synchronised="no")
        self.assertIn("WARN  Clock synchronised (NTP): time service on, not synchronised; check chronyc tracking",
                      output)
        self.assertEqual(report.failed, 0)


class ReadinessStopTests(unittest.TestCase):
    """P2: a stop names every FAIL with its section, last, and what holds port 8080."""

    def test_the_summary_lists_each_fail_with_its_section_at_the_end(self):
        report = acceptance_preflight.Report()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            report.heading("Client/build host")
            report.check(False, "Local port 8080 free", "held by ssh (pid 7)")
            report.heading("todo-standby (192.168.0.108) over SSH")
            report.check(True, "Hostname")
            report.check(False, "SELinux Enforcing", "Permissive")
        self.assertEqual(report.failed, 2)
        self.assertEqual(report.failures, [
            ("Client/build host", "FAIL  Local port 8080 free: held by ssh (pid 7)"),
            ("todo-standby (192.168.0.108) over SSH", "FAIL  SELinux Enforcing: Permissive")])
        with patch.object(acceptance_preflight, "check_local"), patch.object(acceptance_preflight, "check_proxmox"), \
                patch.object(acceptance_preflight, "check_guest"), \
                patch.object(acceptance_preflight, "Report", return_value=report), \
                contextlib.redirect_stdout(output):
            self.assertEqual(acceptance_preflight.main([]), 1)
        tail = output.getvalue().splitlines()[-4:]
        self.assertEqual(tail, ["Failed checks, by section:",
                                "  [Client/build host] FAIL  Local port 8080 free: held by ssh (pid 7)",
                                "  [todo-standby (192.168.0.108) over SSH] FAIL  SELinux Enforcing: Permissive",
                                "NOT READY: 2 check(s) failed. Fix them before starting the agent."])

    def holder(self, listening, containers=""):
        answers = {"ss": (0, listening, ""), "podman": (0, containers, "")}
        with patch.object(acceptance_preflight, "run", side_effect=lambda argv, **_: answers[argv[0]]):
            return acceptance_preflight.port_holder(8080)

    def test_a_container_on_the_port_is_named_with_what_to_do(self):
        text = self.holder('LISTEN 0 4096 0.0.0.0:8080 0.0.0.0:* users:(("rootlessport",pid=2048,fd=10))',
                           "todo-frontend 0.0.0.0:8080->8080/tcp\nkeycloak 127.0.0.1:8443->8443/tcp")
        self.assertIn("held by rootlessport (pid 2048), for the container todo-frontend", text)
        self.assertIn("your own Podman stack", text)

    def test_an_ssh_tunnel_or_an_unknown_holder_says_so(self):
        text = self.holder('LISTEN 0 128 127.0.0.1:8080 0.0.0.0:* users:(("ssh",pid=99,fd=4))')
        self.assertIn("held by ssh (pid 99)", text)
        self.assertIn("tunnel left from an earlier run", text)
        self.assertIn("ss cannot name", self.holder("LISTEN 0 128 127.0.0.1:8080 0.0.0.0:*"))


if __name__ == "__main__":
    unittest.main()


class BuildPythonTests(unittest.TestCase):
    """The python3 the build scripts run must render: run 28 stopped in phase 2 without it."""

    def check(self, answer):
        report = acceptance_preflight.Report()
        with patch.object(acceptance_preflight, "run", return_value=answer) as run, \
                patch.object(acceptance_preflight.shutil, "which", return_value="/venv/bin/python3"), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            acceptance_preflight.check_build_python(report)
        self.assertEqual(run.call_args.args[0][0], "python3")
        return report, output.getvalue()

    def test_the_build_python_with_jinja2_and_pyyaml_passes_and_is_named(self):
        report, output = self.check((0, "/usr/bin/python3", ""))
        self.assertFalse(report.failed)
        self.assertIn("/usr/bin/python3", output)

    def test_a_build_python_without_pyyaml_fails_and_says_which_one(self):
        report, output = self.check((1, "", "ModuleNotFoundError: No module named 'yaml'"))
        self.assertTrue(report.failed)
        self.assertIn("/venv/bin/python3: ModuleNotFoundError: No module named 'yaml'", output)
