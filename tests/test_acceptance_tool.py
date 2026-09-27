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


class FullRunTests(ToolTest):
    """The A2 commands for the full two-VM run."""

    def test_roles_need_every_database_in_the_role(self):
        primary = [("pg_is_in_recovery", (0, "f|off\n"))]
        self.assertEqual(self.tool("--step", "06-5", "check", "roles", "192.168.0.108", "primary", rules=primary)[0], 0)
        self.assertEqual(self.record()[-1]["values"], {"todo": "f|off", "notes": "f|off", "keycloak": "f|off"})
        one_behind = [("keycloak-postgres", (0, "t|on\n")), ("pg_is_in_recovery", (0, "f|off\n"))]
        self.assertEqual(self.tool("--step", "06-5", "check", "roles", "192.168.0.108", "primary",
                                   rules=one_behind)[0], 1)
        standby = [("transaction_read_only", (0, "t|on\n"))]
        self.assertEqual(self.tool("--step", "10-2", "check", "roles", "192.168.0.102", "standby", rules=standby)[0], 0)
        archiving = [("archive_timeout", (0, "f|off|on|1h\n"))]
        self.assertEqual(self.tool("--step", "10-5", "check", "roles", "192.168.0.108", "archiving",
                                   rules=archiving)[0], 0)

    def test_the_write_probe_is_the_guides_rolled_back_insert(self):
        ok = [("psql", (0, "BEGIN\nINSERT 0 1\nROLLBACK\n"))]
        code, fake = self.tool("--step", "06-5", "check", "write-probe", "192.168.0.108", rules=ok)
        self.assertEqual(code, 0)
        self.assertTrue(any("INSERT INTO notes (title) VALUES ('promotion write probe'); ROLLBACK;" in call
                            for call in fake.calls))
        broken = [("notes-postgres", (1, 'ERROR:  column "content" does not exist\n')), ("psql", ok[0][1])]
        self.assertEqual(self.tool("--step", "06-5", "check", "write-probe", "192.168.0.108", rules=broken)[0], 1)

    def test_replication_must_stream_over_tls_for_every_database(self):
        tls = [("pg_stat_ssl", (0, "todo_standby|streaming|t|TLSv1.3\n"))]
        self.assertEqual(self.tool("--step", "04-4", "check", "replication-tls", "192.168.0.102", rules=tls)[0], 0)
        clear = [("notes-postgres", (0, "notes_standby|streaming|f|\n")), tls[0]]
        self.assertEqual(self.tool("--step", "04-4", "check", "replication-tls", "192.168.0.102", rules=clear)[0], 1)
        none = [("keycloak-postgres", (0, "")), tls[0]]
        self.assertEqual(self.tool("--step", "04-4", "check", "replication-tls", "192.168.0.102", rules=none)[0], 1)

    def test_disk_reads_every_size_and_needs_free_space(self):
        def sizes(free):
            return [("du -sk", (0, "".join(f"backup {d} 194560\nwal {d} 82944\n" for d in ("todo", "notes", "keycloak"))
                                + f"free {free}\n"))]
        code, fake = self.tool("--step", "10-8", "check", "disk", "192.168.0.108", rules=sizes(15728640))
        self.assertEqual(code, 0)
        self.assertIn("{{.Mountpoint}}", fake.calls[0])
        self.assertEqual(self.record()[-1]["values"]["free"], "15360 MiB")
        self.assertEqual(self.tool("--step", "10-8", "check", "disk", "192.168.0.108", rules=sizes(1024))[0], 1)

    def test_replication_rule_is_added_and_removed_permanently(self):
        rule = acceptance.replication_rule("192.168.0.108", "192.168.0.102")
        added = [("--list-rich-rules", (0, rule + "\n"))]
        code, fake = self.tool("--step", "04-1", "do", "firewall-replication", "192.168.0.108", "192.168.0.102", "add",
                               rules=added)
        self.assertEqual(code, 0)
        self.assertTrue(any("--permanent --zone=public --add-rich-rule" in call for call in fake.calls))
        self.assertEqual(self.tool("--step", "09-6", "do", "firewall-replication", "192.168.0.108", "192.168.0.102",
                                   "remove", rules=[("--list-rich-rules", (0, ""))])[0], 0)
        # A removal that leaves the rule in place fails (other arguments: a do runs once).
        left = [("--list-rich-rules", (0, acceptance.replication_rule("192.168.0.102", "192.168.0.108") + "\n"))]
        self.assertEqual(self.tool("--step", "09-6", "do", "firewall-replication", "192.168.0.102", "192.168.0.108",
                                   "remove", rules=left)[0], 1)

    def test_proxmox_firewall_is_read_back_and_given_time(self):
        with patch.object(acceptance, "FIREWALL_SETTLE_SECONDS", 20):
            code, fake = self.tool("--step", "05-5", "do", "proxmox-firewall", "107", "on",
                                   rules=[("get /nodes/{node}/qemu/107/firewall/options", (0, '{"enable": 1}'))])
        self.assertEqual(code, 0)
        self.assertTrue(any("set /nodes/{node}/qemu/107/firewall/options enable=1" in call for call in fake.calls))
        self.assertIn("waiting 20s", self.log(self.record()[-1]))
        self.assertEqual(self.tool("--step", "09-12", "do", "proxmox-firewall", "107", "off",
                                   rules=[("firewall/options", (0, '{"enable": 1}'))])[0], 1)

    def test_the_replication_exception_is_found_by_its_comment(self):
        rules = json.dumps([
            {"pos": 0, "comment": "todo-quarantine-ssh-client", "type": "in", "dport": "22", "enable": 1},
            {"pos": 2, "comment": "todo-quarantine-replication", "type": "out", "dport": "5432:5434", "enable": 0}])
        state = {"rules": rules}

        def answer(line):
            if " set " in f" {line} ":
                state["rules"] = state["rules"].replace('"enable": 0}', '"enable": 1}')
                return 0, "null"
            return 0, state["rules"]
        code, fake = self.tool("--step", "09-8", "do", "replication-exception", "107", "on",
                               rules=[("pve_lab.py", answer)])
        self.assertEqual(code, 0)
        self.assertTrue(any("set /nodes/{node}/qemu/107/firewall/rules/2 enable=1" in call for call in fake.calls))
        missing = json.dumps([{"pos": 0, "comment": "something else", "type": "out", "dport": "5432:5434"}])
        code, fake = self.tool("--step", "09-8", "do", "replication-exception", "107", "off",
                               rules=[("pve_lab.py", (0, missing))])
        self.assertEqual(code, 1)
        self.assertFalse(any(" set " in call for call in fake.calls))


class InterruptedStepTests(ToolTest):
    """A do that crashes or is killed must still block a blind second run."""

    def test_an_unexpected_error_is_recorded_and_blocks_the_next_run(self):
        arguments = ("--step", "06-1", "do", "reboot", "107", "192.168.0.102", "app")
        with patch.object(acceptance, "boot_id", side_effect=OSError("ssh vanished")):
            self.assertEqual(self.tool(*arguments)[0], 1)
        started, failed = self.record()
        self.assertEqual((started["result"], failed["result"]), ("STARTED", "FAIL"))
        self.assertIn("OSError: ssh vanished", self.log(failed))
        code, fake = self.tool(*arguments)
        self.assertEqual(code, 3)
        self.assertEqual(fake.calls, [])

    def test_a_started_do_without_a_result_blocks_until_approved(self):
        self.run_directory.mkdir(parents=True)
        (self.run_directory / "record.jsonl").write_text(json.dumps({
            "kind": "do", "command": "reboot", "arguments": ["107", "192.168.0.102", "app"],
            "result": "STARTED", "log": "logs/06-1-do-reboot.log", "step": "06-1", "values": {},
            "approved": "", "time": "t"}) + "\n")
        arguments = ("--step", "06-1", "do", "reboot", "107", "192.168.0.102", "app")
        self.assertEqual(self.tool(*arguments)[0], 3)
        self.assertIn("started but never finished", self.log(self.record()[-1]))
        boots = iter(["old\n", "new\n"])
        rules = [("boot_id", lambda line: (0, next(boots))), ("wait-ready", (0, "READY: x\n"))]
        self.assertEqual(self.tool("--operator-approved", "VM checked by hand", *arguments, rules=rules)[0], 0)

    def test_checks_write_no_started_line(self):
        self.tool("--step", "04-2", "check", "headers", rules=[("curl", (0, APP_HEADERS))])
        self.assertEqual([entry["result"] for entry in self.record()], ["PASS"])


if __name__ == "__main__":
    unittest.main()
