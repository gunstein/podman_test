"""deploy/scripts/acceptance.py with every external command faked."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/scripts"))
import acceptance  # noqa: E402

FINGERPRINT = ":".join(["AB"] * 32)
OTHER = ":".join(["CD"] * 32)
APP_HEADERS = ("HTTP/1.1 200 OK\r\nX-Content-Type-Options: nosniff\r\n"
               "Strict-Transport-Security: max-age=31536000\r\n"
               "Content-Security-Policy: default-src 'self'; script-src 'self'; "
               "connect-src 'self' https://todo.test:8443; frame-ancestors 'none'; object-src 'none'\r\n"
               "X-Frame-Options: DENY\r\nReferrer-Policy: strict-origin-when-cross-origin\r\n\r\n")
AUTH_HEADERS = "HTTP/1.1 200 OK\r\nStrict-Transport-Security: max-age=31536000\r\n\r\n"


class Fake:
    """Answers each command by its first matching (text in the command line) rule."""

    def __init__(self, rules):
        self.rules, self.calls = rules, []

    def __call__(self, argv, input=None, **kwargs):
        line = " ".join(argv) + " " + (input or "")
        self.calls.append(line)
        for needle, answer in self.rules:
            if needle in line:
                code, out = answer(line) if callable(answer) else answer
                return subprocess.CompletedProcess(argv, code, out, "")
        return subprocess.CompletedProcess(argv, 0, "", "")


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.runs)])
        secret = self.runs / "xdg/todo-acceptance"
        secret.mkdir(parents=True)
        (secret / "e2e-password").write_text("not-a-real-password\n")
        self.environment = patch.dict(os.environ, {"ACCEPTANCE_RUNS": str(self.runs),
                                                   "XDG_RUNTIME_DIR": str(self.runs / "xdg")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.run_directory = self.runs / "run-1"

    def tool(self, *arguments, rules=()):
        fake = Fake(list(rules))
        with patch.object(acceptance.subprocess, "run", fake), patch.object(acceptance.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            code = acceptance.main(["--run", "run-1", *arguments])
        return code, fake

    def record(self):
        return [json.loads(line) for line in (self.run_directory / "record.jsonl").read_text().splitlines()]

    def log(self, entry):
        return (self.run_directory / entry["log"]).read_text()


class RecordTests(ToolTest):
    def test_each_call_has_its_own_log_and_record_line(self):
        rules = [("wait-ready", (0, "READY: app on todo-primary after 3s\n"))]
        self.assertEqual(self.tool("--step", "03-4", "check", "services", "192.168.0.102", "app", rules=rules)[0], 0)
        self.assertEqual(self.tool("--step", "03-4", "check", "services", "192.168.0.102", "app", rules=rules)[0], 0)
        first, second = self.record()
        self.assertEqual(first["log"], "logs/03-4-check-services.log")
        self.assertEqual(second["log"], "logs/03-4-check-services-2.log")
        self.assertEqual(first["result"], "PASS")
        log = self.log(first)
        self.assertIn("$ ssh -o BatchMode=yes", log)
        self.assertIn("exit=0", log)
        self.assertIn("RESULT: PASS", log)

    def test_a_failed_do_is_refused_until_the_operator_approves(self):
        failing = [("firewall-cmd --zone=public --list-rich-rules", (0, "")),
                   ("--permanent --zone=public --list-rich-rules", (0, ""))]
        arguments = ("--step", "03-2", "do", "firewall-https", "192.168.0.102", "192.168.0.100")
        self.assertEqual(self.tool(*arguments, rules=failing)[0], 1)
        code, fake = self.tool(*arguments)
        self.assertEqual(code, 3)
        self.assertEqual(fake.calls, [])
        self.assertIn("needs --operator-approved", self.log(self.record()[-1]))
        code, fake = self.tool("--operator-approved", "rule added by hand, checked", *arguments, rules=failing)
        self.assertEqual(code, 1)
        self.assertTrue(fake.calls)
        self.assertEqual(self.record()[-1]["approved"], "rule added by hand, checked")

    def test_markers_run_once_per_phase(self):
        rules = [("create_markers.py", (0, "MARKER 'acceptance run-1 phase3': todo id=3 note id=4\n"))]
        self.assertEqual(self.tool("--step", "03-8", "do", "markers", "phase3", rules=rules)[0], 0)
        self.assertEqual(self.record()[-1]["values"],
                         {"title": "acceptance run-1 phase3", "todo_id": 3, "note_id": 4})
        self.assertEqual(self.tool("--step", "03-8", "do", "markers", "phase3", rules=rules)[0], 3)
        self.assertEqual(self.tool("--step", "04-5", "do", "markers", "phase4", rules=rules)[0], 1)

    def test_bad_arguments_never_reach_a_command(self):
        for arguments in (("check", "services", "192.168.0.102; rm -rf ~", "app"),
                          ("check", "services", "192.168.0.102", "primary"),
                          ("do", "markers", "phase3 x"),
                          ("do", "services", "192.168.0.102", "app")):
            with self.subTest(arguments), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.tool("--step", "03-4", *arguments)
        self.assertFalse((self.run_directory / "record.jsonl").exists())


class CheckTests(ToolTest):
    def test_services_fail_on_not_ready_failed_units_or_bad_nginx(self):
        for rules, message in (
                ([("wait-ready", (1, "NOT READY after 300s: container nginx\n"))], "READY"),
                ([("wait-ready", (0, "READY: x\n")), ("--failed", (0, "todo-app.service failed\n"))], "failed"),
                ([("wait-ready", (0, "READY: x\n")), ("nginx -t", (1, "emerg\n"))], "nginx")):
            with self.subTest(message):
                self.assertEqual(self.tool("--step", "03-4", "check", "services", "192.168.0.102", "app",
                                           rules=rules)[0], 1)
        rules = [("wait-ready", (0, "READY: x\n")), ("nginx -t", (1, "no nginx on a standby\n"))]
        self.assertEqual(self.tool("--step", "10-2", "check", "services", "192.168.0.102", "standby",
                                   rules=rules)[0], 0)

    def test_headers_pass_and_a_wrong_connect_src_fails(self):
        good = [("/auth/", (0, AUTH_HEADERS)), ("curl", (0, APP_HEADERS))]
        self.assertEqual(self.tool("--step", "03-3", "check", "headers", rules=good)[0], 0)
        wrong = [("/auth/", (0, AUTH_HEADERS)), ("curl", (0, APP_HEADERS.replace(" https://todo.test:8443;", ";")))]
        self.assertEqual(self.tool("--step", "03-3", "check", "headers", rules=wrong)[0], 1)
        self.assertIn("FAIL: https://todo.test:8443/: connect-src", self.log(self.record()[-1]))

    def test_the_ca_is_saved_and_must_not_change_on_the_same_host(self):
        def ca_rules(value):
            return [("cat /var/lib/todo-tls/ca.crt", (0, "-----BEGIN CERTIFICATE-----\n")),
                    ("-fingerprint", (0, f"sha256 Fingerprint={value}\n"))]
        self.assertEqual(self.tool("--step", "03-3", "check", "ca", "192.168.0.102", rules=ca_rules(FINGERPRINT))[0], 0)
        self.assertTrue((self.run_directory / "ca.crt").is_file())
        self.assertEqual(self.tool("--step", "03-9", "check", "ca", "192.168.0.102", rules=ca_rules(OTHER))[0], 1)
        self.assertEqual(self.tool("--step", "07-2", "check", "ca", "192.168.0.108", rules=ca_rules(OTHER))[0], 0)

    def test_browser_needs_the_ca_and_treats_a_skip_as_failure(self):
        self.assertEqual(self.tool("--step", "03-7", "check", "browser")[0], 3)
        (self.run_directory / "ca.crt").write_text("ca")
        passed = [("pytest", (0, "..\n2 passed in 2.10s\n"))]
        code, fake = self.tool("--step", "03-7", "check", "browser", rules=passed)
        self.assertEqual(code, 0)
        self.assertEqual(sum("pytest" in call for call in fake.calls), 3)
        self.assertNotIn("not-a-real-password", self.log(self.record()[-1]))
        skipped = [("pytest", (0, "s.\n1 passed, 1 skipped in 2.10s\n"))]
        self.assertEqual(self.tool("--step", "03-7", "check", "browser", rules=skipped)[0], 1)

    def test_markers_on_a_host_are_compared_with_the_recorded_ones(self):
        created = [("create_markers.py", (0, "MARKER 'acceptance run-1 phase3': todo id=3 note id=4\n"))]
        self.tool("--step", "03-8", "do", "markers", "phase3", rules=created)
        present = [("FROM todos", (0, "3|acceptance run-1 phase3\n")), ("FROM notes", (0, "4|acceptance run-1 phase3\n"))]
        self.assertEqual(self.tool("--step", "03-9", "check", "markers", "192.168.0.102", rules=present)[0], 0)
        missing = [("FROM todos", (0, "3|acceptance run-1 phase3\n")), ("FROM notes", (0, ""))]
        self.assertEqual(self.tool("--step", "03-9", "check", "markers", "192.168.0.102", rules=missing)[0], 1)

    def test_clean_host(self):
        clean = [("getenforce", (0, "Enforcing\nactive\nactive\nactive\ntrue\n"
                                    "containers=0 volumes=0 secrets=0\nquadlet=none\nabc123\n"))]
        self.assertEqual(self.tool("--step", "01-4", "check", "clean-host", "192.168.0.102", rules=clean)[0], 0)
        self.assertEqual(self.record()[-1]["values"], {"machine_id": "abc123"})
        used = [("getenforce", (0, "Enforcing\nactive\nactive\nactive\ntrue\n"
                                   "containers=9 volumes=4 secrets=16\nquadlet=present\nabc123\n"))]
        self.assertEqual(self.tool("--step", "01-4", "check", "clean-host", "192.168.0.102", rules=used)[0], 1)


class DoTests(ToolTest):
    def test_firewall_rule_must_be_running_and_permanent(self):
        rule = ('rule family="ipv4" source address="192.168.0.100/32" destination address="192.168.0.102" '
                'port port="8443" protocol="tcp" accept')
        both = [("--list-rich-rules", (0, rule + "\n"))]
        code, fake = self.tool("--step", "03-2", "do", "firewall-https", "192.168.0.102", "192.168.0.100", rules=both)
        self.assertEqual(code, 0)
        self.assertTrue(any("--permanent --zone=public --add-rich-rule" in call and "--reload" in call
                            for call in fake.calls))
        runtime_only = [("--permanent --zone=public --list-rich-rules", (0, "")), ("--list-rich-rules", (0, rule))]
        self.assertEqual(self.tool("--step", "07-1", "do", "firewall-https", "192.168.0.108", "192.168.0.100",
                                   rules=[(r[0], (0, r[1][1].replace("0.102", "0.108"))) for r in runtime_only])[0], 1)

    def test_reboot_waits_for_a_new_boot_id_then_checks_services(self):
        boots = iter(["old\n", "old\n", "", "new\n"])
        rules = [("boot_id", lambda line: (0, next(boots))), ("wait-ready", (0, "READY: x\n"))]
        code, fake = self.tool("--step", "03-9", "do", "reboot", "107", "192.168.0.102", "app", rules=rules)
        self.assertEqual(code, 0)
        self.assertEqual(self.record()[-1]["values"], {"before": "old", "after": "new"})
        self.assertTrue(any("/qemu/107/status/reboot" in call for call in fake.calls))

    def test_a_reboot_that_never_comes_back_fails(self):
        rules = [("boot_id", (0, "old\n")), ("wait-ready", (0, "READY: x\n"))]
        with patch.object(acceptance, "REBOOT_TIMEOUT", 0):
            code, _ = self.tool("--step", "03-9", "do", "reboot", "107", "192.168.0.102", "app", rules=rules)
        self.assertEqual(code, 1)

    def test_rollback_starts_a_stopped_vm_and_waits_for_ssh(self):
        rules = [("status/current", (0, '{"status": "stopped"}')), ("boot_id", (0, "id\n")),
                 ("firewall/options", (0, '{"enable": 0}')),
                 ("/config", (0, '{"onboot": 0, "net0": "virtio=AA,bridge=vmbr0,firewall=1"}'))]
        code, fake = self.tool("--step", "01-1", "do", "rollback", "107", "clean-agent", "192.168.0.102", rules=rules)
        self.assertEqual(code, 0)
        self.assertEqual(self.record()[-1]["values"]["onboot"], 0)
        self.assertTrue(any("snapshot/clean-agent/rollback" in call for call in fake.calls))
        self.assertTrue(any("/qemu/107/status/start" in call for call in fake.calls))

    def test_rollback_fails_when_an_earlier_run_left_the_firewall_on(self):
        rules = [("status/current", (0, '{"status": "running"}')), ("boot_id", (0, "id\n")),
                 ("firewall/options", (0, '{"enable": 1}')),
                 ("/config", (0, '{"net0": "virtio=AA,bridge=vmbr0,link_down=1"}'))]
        code, fake = self.tool("--step", "01-1", "do", "rollback", "107", "clean-agent", "192.168.0.102", rules=rules)
        self.assertEqual(code, 1)
        self.assertFalse(any("status/start" in call for call in fake.calls))
        log = self.log(self.record()[-1])
        self.assertIn("FAIL: VM 107 firewall is off", log)
        self.assertIn("FAIL: every network link of VM 107 is up", log)


if __name__ == "__main__":
    unittest.main()
