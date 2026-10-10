"""deploy/scripts/lab/acceptance.py with every external command faked."""
import contextlib
import datetime
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/scripts/lab"))
import acceptance  # noqa: E402

FINGERPRINT = ":".join(["AB"] * 32)
OTHER = ":".join(["CD"] * 32)
APP_HEADERS = ("HTTP/1.1 200 OK\r\nX-Content-Type-Options: nosniff\r\n"
               "Strict-Transport-Security: max-age=31536000\r\n"
               "Content-Security-Policy: default-src 'self'; script-src 'self'; "
               "connect-src 'self' https://auth.test:8443; frame-ancestors 'none'; object-src 'none'\r\n"
               "X-Frame-Options: DENY\r\nReferrer-Policy: strict-origin-when-cross-origin\r\n\r\n")
AUTH_HEADERS = "HTTP/1.1 200 OK\r\nStrict-Transport-Security: max-age=31536000\r\n\r\n"


REVISION = "b9bffbfd175509936f61994a1ba087e464e476f7"
GIT = [("rev-parse", (0, REVISION + "\n")), ("status --porcelain", (0, ""))]


REAL_RUN = subprocess.run  # the tests patch subprocess.run; the shell checks below need the real one


def valid_bash(script, what):
    """Fail the test unless bash can parse script, as a remote or local shell would have to."""
    result = REAL_RUN(["bash", "-n"], input=script, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"{what} is not valid bash: {result.stderr.strip()}\n{script}")


class Fake:
    """Answers each command by its first matching (text in the command line) rule.

    Git is answered with a clean checkout at REVISION unless a rule says
    otherwise, and git calls are not listed in calls.

    It also checks what a real shell would do with each command, because a
    fake that only matches text let run 13's quoting bug through: for ssh, the
    remote command must split into bash -s -- and its arguments exactly as the
    tool meant, and the script on stdin must parse; for bash -c, the script
    must parse.
    """

    def __init__(self, rules):
        self.rules, self.calls = list(rules) + GIT, []

    def __call__(self, argv, input=None, **kwargs):
        if argv[0] == "ssh":
            words = shlex.split(argv[-1])
            if words[:3] != ["bash", "-s", "--"]:
                raise AssertionError(f"the remote command does not start bash -s --: {argv[-1]}")
            valid_bash(argv[-1], "the remote command")
            valid_bash(input or "", "the script sent over ssh")
        elif argv[:2] == ["bash", "-c"]:
            valid_bash(argv[2], "the bash -c script")
        line = " ".join(argv) + " " + (input or "")
        if argv[0] != "git":
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
        # Checks that wait for replication judge at once here; tests that need the wait set it.
        streaming = patch.object(acceptance, "STREAMING_TIMEOUT", 0)
        streaming.start()
        self.addCleanup(streaming.stop)

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
        rules = [("wait-ready", (0, "READY: x\n")), ("nginx -t", (1, "no nginx on a standby\n")),
                 ("is-active", (3, "inactive\ninactive\ninactive\ninactive\n"))]
        self.assertEqual(self.tool("--step", "10-2", "check", "services", "192.168.0.102", "standby",
                                   rules=rules)[0], 0)
        rules[2] = ("is-active", (0, "active\ninactive\ninactive\ninactive\n"))
        self.assertEqual(self.tool("--step", "10-2", "check", "services", "192.168.0.102", "standby",
                                   rules=rules)[0], 1)

    def test_a_failed_podman_health_check_run_is_listed_not_failed(self):
        """Run 19: one health-check run failed while a container started; the check failed on it."""
        unit = "327f5281df52db4abfe1ca62fd6336959bc246c9be4db4a5943ecf594f7755ed-29e5e1da076debce.service"
        line = f"{unit} loaded failed failed /usr/bin/podman healthcheck run 327f5281df52\n"
        ready = ("wait-ready", (0, "READY: x\n"))
        self.assertEqual(self.tool("--step", "07-5", "check", "services", "192.168.0.108", "app",
                                   rules=[ready, ("--failed", (0, line))])[0], 0)
        entry = acceptance.read_record(self.run_directory)[-1]
        self.assertEqual(entry["values"], {"failed_health_check_runs": 1})
        self.assertEqual(self.tool("--step", "07-5", "check", "services", "192.168.0.108", "app",
                                   rules=[ready, ("--failed", (0, line + "todo-app.service loaded failed failed\n"))]
                                   )[0], 1)

    def test_headers_pass_and_a_wrong_connect_src_fails(self):
        good = [("/auth/", (0, AUTH_HEADERS)), ("curl", (0, APP_HEADERS))]
        self.assertEqual(self.tool("--step", "03-3", "check", "headers", rules=good)[0], 0)
        wrong = [("/auth/", (0, AUTH_HEADERS)), ("curl", (0, APP_HEADERS.replace(" https://auth.test:8443;", ";")))]
        self.assertEqual(self.tool("--step", "03-3", "check", "headers", rules=wrong)[0], 1)
        self.assertIn("FAIL: https://todo.test:8443/: connect-src", self.log(self.record()[-1]))

    def test_the_ca_is_saved_and_must_not_change_on_the_same_host(self):
        def ca_rules(value):
            return [("cat /var/lib/platform-tls/ca.crt", (0, "-----BEGIN CERTIFICATE-----\n")),
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

    def test_monitor_needs_the_timer_and_the_expected_outcome(self):
        timer = ("is-enabled platform-dr-check.timer", (0, "enabled\nactive\n"))
        passed = ("systemctl --user start platform-dr-check.service",
                  (0, "exit=0\ntodo: primary, 1 standby streaming over TLS, WAL archiving off\nDisk: 40% free (9000 MiB)\n"
                      "Ready to take over: offline bundle aaaaaaaaaaaa with 7 image archives, all 13 DR secrets\n"))
        not_ready = ("systemctl --user start platform-dr-check.service",
                     (0, "exit=0\ntodo: primary, 1 standby streaming over TLS, WAL archiving off\n"
                         "Disk: 40% free (9000 MiB)\n"))
        failed = ("systemctl --user start platform-dr-check.service",
                  (0, "exit=1\nDisk: 40% free (9000 MiB)\nERROR: todo: no standby streams from this primary over TLS\n"))
        self.assertEqual(self.tool("--step", "05-7a", "check", "monitor", "192.168.0.102", "ok",
                                   rules=[timer, passed])[0], 0)
        self.assertTrue(self.record()[-1]["values"]["ready"].startswith("Ready to take over: offline bundle"))
        self.assertEqual(self.tool("--step", "06-15", "check", "monitor", "192.168.0.108", "alert",
                                   rules=[timer, failed])[0], 0)
        self.assertEqual(self.record()[-1]["values"]["problems"],
                         ["todo: no standby streams from this primary over TLS"])
        for rules in ([timer, failed], [timer, not_ready], [("is-enabled", (1, "disabled\ninactive\n")), passed],
                      [timer, ("systemctl --user start", (0, "exit=0\n"))]):
            with self.subTest(rules=rules[-1][1]):
                self.assertEqual(self.tool("--step", "05-7a", "check", "monitor", "192.168.0.102", "ok",
                                           rules=rules)[0], 1)
        self.assertEqual(self.tool("--step", "06-15", "check", "monitor", "192.168.0.108", "alert",
                                   rules=[timer, passed])[0], 1)

    def test_a_failed_dr_check_is_not_a_failed_service(self):
        ready = ("wait-ready", (0, "READY: x\n"))
        line = "platform-dr-check.service loaded failed failed Todo DR check\n"
        self.assertEqual(self.tool("--step", "07-5", "check", "services", "192.168.0.108", "app",
                                   rules=[ready, ("--failed", (0, line))])[0], 0)
        self.assertEqual(self.record()[-1]["values"], {"dr_check": "failed (see check monitor)"})
        backup = "platform-backup.service loaded failed failed Todo nightly base backup\n"
        self.assertEqual(self.tool("--step", "07-5", "check", "services", "192.168.0.108", "app",
                                   rules=[ready, ("--failed", (0, line + backup))])[0], 1)

    def test_the_nightly_backup_backs_up_every_database(self):
        timer = ("is-enabled platform-backup.timer", (0, "enabled\nactive\n"))
        journal = "".join(f"{d}: verified base backup base-20261002T200000Z; deleted 0 older than 7 days\n"
                          for d in ("todo", "notes", "keycloak"))
        code, fake = self.tool("--step", "08-16", "do", "backup-nightly", "192.168.0.108",
                               rules=[timer, ("start platform-backup.service", (0, "exit=0\n" + journal))])
        self.assertEqual(code, 0)
        self.assertEqual(self.record()[-1]["values"]["notes"], "base-20261002T200000Z")
        self.assertIn("journalctl _SYSTEMD_USER_UNIT=platform-backup.service", fake.calls[-1])
        self.assertEqual(self.tool("--step", "08-16", "do", "backup-nightly", "192.168.0.108", "--operator-approved",
                                   "retry in the test",
                                   rules=[timer, ("start platform-backup.service", (0, "exit=1\n"))])[0], 1)

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


class ReportTests(ToolTest):
    """acceptance.py report builds the run record from record.jsonl and the logs."""
    def product_log(self, name, *lines):
        (self.run_directory / "logs" / name).write_text("\n".join(lines) + "\n")

    def matching_guide(self):
        """A guide that asks for exactly what this run did."""
        lines = []
        for entry in acceptance.read_record(self.run_directory):
            if entry["result"] != "STARTED":
                lines.append(f'$A --step {entry["step"]} {entry["kind"]} {entry["command"]} '
                             + " ".join(entry["arguments"]))
        tool_logs = {entry["log"] for entry in acceptance.read_record(self.run_directory)}
        for path in sorted((self.run_directory / "logs").glob("*.log")):
            if f"logs/{path.name}" not in tool_logs:
                lines.append(f'vm {path.stem} 192.168.0.102 "true"')
        return "\n".join(dict.fromkeys(lines)) + "\n"

    def report(self, rules=(), guide=None):
        text = self.matching_guide() if guide is None else guide
        fake = Fake(list(rules) + [("show ", (0, text))])
        with patch.object(acceptance.subprocess, "run", fake), contextlib.redirect_stdout(io.StringIO()):
            code = acceptance.main(["--run", "run-1", "report", "full"])
        return code, (self.run_directory / "REPORT.md").read_text()

    def test_a_clean_run_passes_and_takes_every_value_from_the_record(self):
        self.tool("--step", "03-8", "do", "markers", "phase3",
                  rules=[("create_markers.py", (0, "MARKER 'acceptance run-1 phase3': todo id=3 note id=4\n"))])
        self.tool("--step", "04-2", "check", "headers", rules=[("curl", (0, APP_HEADERS))])
        self.product_log("03-2-install.log", "Preflight checks passed.", '{"changed": true}', "exit=0")
        self.product_log("04-3-preflight-refused.log", "app-ops: no rich rule", "exit=1")
        code, text = self.report()
        self.assertEqual(code, 0)
        self.assertIn("**From the record: ALL STEPS PASS.**", text)
        self.assertIn("b9bffbfd175509936f61994a1ba087e464e476f7", text)
        self.assertIn("todo_id=3", text)
        self.assertIn('| logs/03-2-install.log | exit=0 | {"changed": true} |', text)
        self.assertIn("- Nothing.", text)
        self.assertNotIn("STARTED", text)

    def test_failures_refusals_approvals_and_bad_product_logs_need_attention(self):
        failing = [("--list-rich-rules", (0, ""))]
        arguments = ("--step", "03-3", "do", "firewall-https", "192.168.0.102", "192.168.0.100")
        self.tool(*arguments, rules=failing)
        self.tool(*arguments)
        self.tool("--operator-approved", "checked by hand", *arguments, rules=failing)
        self.product_log("02-1-build.log", "building", "exit=2")
        self.product_log("04-3-preflight-refused.log", "app-ops: no rich rule", "exit=0")
        self.product_log("02-2-transfer.log", "no exit line at all")
        code, text = self.report()
        self.assertEqual(code, 1)
        self.assertIn("NOT CLEAN", text)
        for expected in ("03-3 do firewall-https: FAIL", "03-3 do firewall-https: REFUSED",
                         'operator approval "checked by hand"', "logs/02-1-build.log: exit=2",
                         "logs/02-2-transfer.log: no exit= line",
                         "logs/04-3-preflight-refused.log: exit=0, expected exit=1"):
            self.assertIn(expected, text)

    def test_an_unfinished_do_needs_attention(self):
        self.run_directory.mkdir(parents=True)
        (self.run_directory / "logs").mkdir()
        (self.run_directory / "record.jsonl").write_text(json.dumps({
            "kind": "do", "command": "reboot", "arguments": ["107", "192.168.0.102", "app"], "result": "STARTED",
            "log": "logs/06-1-do-reboot.log", "step": "06-1", "values": {}, "approved": "", "time": "t",
            "revision": REVISION, "clean": True}) + "\n")
        (self.run_directory / "logs/06-1-do-reboot.log").write_text("# half a reboot\n")
        code, text = self.report()
        self.assertEqual(code, 1)
        self.assertIn("06-1 do reboot: started but never finished", text)
        self.assertNotIn("| logs/06-1-do-reboot.log |", text.split("## Other logs")[1])

    def test_repeated_checks_are_listed_but_allowed(self):
        rules = [("wait-ready", (0, "READY: x\n"))]
        for step in ("03-4", "06-5"):
            self.tool("--step", step, "check", "services", "192.168.0.102", "app", rules=rules)
        code, text = self.report()
        self.assertEqual(code, 0)
        self.assertIn("check services 192.168.0.102 app: steps 03-4, 06-5", text)

    def test_the_revision_is_the_one_each_step_ran_from_not_the_one_checked_out_later(self):
        rules = [("wait-ready", (0, "READY: x\n"))]
        self.tool("--step", "03-4", "check", "services", "192.168.0.102", "app", rules=rules)
        self.assertEqual(self.record()[-1]["revision"], REVISION)
        code, text = self.report(rules=[("rev-parse", (0, "0" * 40 + "\n"))])
        self.assertEqual(code, 0)
        self.assertIn(f"`{REVISION}`, checkout clean at every step", text)
        self.assertNotIn("0" * 40, text)

    def test_a_step_on_another_revision_or_a_dirty_checkout_needs_attention(self):
        rules = [("wait-ready", (0, "READY: x\n"))]
        self.tool("--step", "03-4", "check", "services", "192.168.0.102", "app", rules=rules)
        self.tool("--step", "06-5", "check", "services", "192.168.0.102", "app",
                  rules=rules + [("status --porcelain", (0, " M deploy/scripts/lab/acceptance.py\n"))])
        self.assertIn("is not clean", self.log(self.record()[-1]))
        code, text = self.report()
        self.assertEqual(code, 1)
        self.assertIn("did not all run from one clean checkout", text)
        self.assertIn(f"{REVISION} (NOT clean)", text)



    def test_the_run_is_compared_with_the_guide(self):
        """Run 15 replaced two guide steps with four others; report said ALL STEPS PASS."""
        closed = [("CLOSED", (0, "CLOSED: 192.168.0.102\n"))]
        guide = ("$A --step 06-3 check ports-closed 192.168.0.102 client\n"
                 "vm 06-6-preflight 192.168.0.108 'python3 /opt/platform/bin/app_dr.py preflight'\n"
                 '$A --step 09-12b do onboot 107 "$ONBOOT"\n')
        self.tool("--step", "06-4", "check", "connect", "client", "192.168.0.102", "22", "blocked",
                  rules=[("/dev/tcp", (124, ""))])
        self.tool("--step", "09-12b", "do", "onboot", "107", "0", rules=[("get /nodes", (0, '{"onboot": 0}'))])
        self.product_log("06-8-preflight.log", "Preflight passed", "exit=0")
        code, text = self.report(rules=closed, guide=guide)
        self.assertEqual(code, 1)
        for expected in ("guide step 06-3 `check ports-closed 192.168.0.102 client` did not run",
                         "step 06-4 `check connect client 192.168.0.102 22 blocked` is not in the guide",
                         "guide log logs/06-6-preflight.log is missing",
                         "log logs/06-8-preflight.log is not in the guide"):
            self.assertIn(expected, text)
        self.assertNotIn("09-12b", text.split("## Needs attention")[1].split("## Repeated")[0])

    def test_a_step_with_other_arguments_than_the_guide_needs_attention(self):
        self.tool("--step", "04-15", "check", "roles", "192.168.0.108", "primary",
                  rules=[("pg_is_in_recovery", (0, "f|off\n"))])
        code, text = self.report(guide="$A --step 04-15 check roles 192.168.0.108 standby\n")
        self.assertEqual(code, 1)
        self.assertIn("step 04-15 ran `check roles 192.168.0.108 primary`, the guide says "
                      "`check roles 192.168.0.108 standby`", text)

    def add_record(self, **changes):
        """Append a copy of the last finished record with some fields changed."""
        record = self.run_directory / "record.jsonl"
        last = json.loads(record.read_text().splitlines()[-1])
        record.write_text(record.read_text() + json.dumps({**last, **changes}) + "\n")

    def test_every_record_under_a_label_is_compared_not_only_the_first(self):
        """Code review: a second, different command under a correct label passed unseen."""
        self.tool("--step", "05-8-4a", "do", "power", "107", "shutdown",
                  rules=[("status/shutdown", (0, '{"data": "UPID"}')), ("status/current", (0, '{"status": "stopped"}'))])
        self.add_record(arguments=["107", "start"])
        code, text = self.report(guide="$A --step 05-8-4a do power 107 shutdown\n")
        self.assertEqual(code, 1)
        attention = text.split("## Needs attention")[1]
        self.assertIn("step 05-8-4a ran `do power 107 start`, the guide says `do power 107 shutdown`", attention)
        self.assertIn("step 05-8-4a ran a do 2 times; a do step runs once", attention)

    def test_a_check_may_repeat_under_its_label_but_not_with_other_arguments(self):
        self.tool("--step", "04-15", "check", "roles", "192.168.0.108", "standby",
                  rules=[("pg_is_in_recovery", (0, "t|on\n"))])
        self.add_record()
        guide = "$A --step 04-15 check roles 192.168.0.108 standby\n"
        self.assertEqual(self.report(guide=guide)[0], 0)
        self.add_record(arguments=["192.168.0.108", "primary"])
        code, text = self.report(guide=guide)
        self.assertEqual(code, 1)
        self.assertIn("step 04-15 ran `check roles 192.168.0.108 primary`", text)

    def test_a_failover_that_found_the_group_promoted_needs_attention(self):
        """Run 20: a first failover attempt promoted, a second one hid it; the log said promoted_now false."""
        self.tool("--step", "06-12", "check", "roles", "192.168.0.108", "primary",
                  rules=[("pg_is_in_recovery", (0, "f|off\n"))])
        self.product_log("06-10-failover.log", "app-ops failover: users done",
                         '{"changed": true, "promoted_now": false, "users": {}}', "exit=0")
        code, text = self.report()
        self.assertEqual(code, 1)
        self.assertIn('logs/06-10-failover.log: "promoted_now": false', text.split("## Needs attention")[1])

    def test_both_guides_parse(self):
        for name, path in acceptance.GUIDES.items():
            tool, logs = acceptance.guide_steps((ROOT / path).read_text())
            self.assertTrue(tool and logs, name)

    def test_report_needs_a_run_and_nothing_else(self):
        for arguments in (["--run", "run-1", "report", "full"], ["--run", "..", "report", "full"],
                          ["--run", "run-1", "report"], ["--run", "run-1", "report", "full", "extra"]):
            with self.subTest(arguments), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                acceptance.main(arguments)


class TimeTests(ToolTest):
    def test_the_report_times_each_phase_and_the_gaps_between_steps(self):
        """A1: where a run spends its time, in steps or between them, read from the logs."""
        logs = self.run_directory / "logs"
        logs.mkdir(parents=True)
        start = datetime.datetime(2026, 10, 4, 18, 0, tzinfo=datetime.timezone.utc)
        for name, offset, length in (("03-2-install", 0, 300), ("03-3-check-services", 360, 60),
                                     ("04-1-trust-ops", 600, 30), ("04-10-bootstrap", 700, 400),
                                     ("04-11-status", 750, 10)):
            path = logs / f"{name}.log"
            began = start + datetime.timedelta(seconds=offset)
            first = f"# start {began.isoformat()}" if "-check-" not in name else f"# {began.isoformat()} check"
            path.write_text(f"{first}\nexit=0\n")
            os.utime(path, (began.timestamp() + length,) * 2)
        (logs / "05-1-untimed.log").write_text("no time here\n")
        text = "\n".join(acceptance.timing(self.run_directory))
        # 03: 0-300 and 360-420, a 60 s gap; 04: 600-630 and 700-1100 (background) after 180 s and 70 s gaps,
        # and 750-760 inside the background step.
        self.assertIn("| 03 | 2 | 7 min 00 s | 6 min 00 s | 1 min 00 s |", text)
        self.assertIn("| 04 | 3 | 8 min 20 s | 7 min 20 s | 4 min 10 s |", text)
        self.assertIn("| all | 5 | 18 min 20 s | 13 min 20 s | 5 min 10 s |", text)
        self.assertIn("Slowest steps: 04-10-bootstrap 6 min 40 s, 03-2-install 5 min 00 s,", text)
        self.assertEqual(acceptance.minutes(3725), "1 h 02 min")


class FailoverTimeTests(ToolTest):
    """G3: the drill shows that Trondheim serves within 30 minutes of the fence."""
    report = ReportTests.report
    matching_guide = ReportTests.matching_guide

    def logs(self, length):
        self.tool("--step", "04-2", "check", "headers", rules=[("curl", (0, APP_HEADERS))])
        logs = self.run_directory / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        start = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=datetime.timezone.utc)
        for name, offset, duration in (("06-3-do-fence", 0, 20), ("06-10-failover", 60, 120),
                                       ("07-8-check-browser", length - 30, 30)):
            path = logs / f"{name}.log"
            began = start + datetime.timedelta(seconds=offset)
            path.write_text(f"# {began.isoformat()} step\nexit=0\n")
            os.utime(path, (began.timestamp() + duration,) * 2)

    def test_the_time_from_the_fence_to_the_browser_test_is_reported(self):
        self.logs(9 * 60 + 5)
        self.assertEqual(acceptance.failover_time(self.run_directory), 545)
        code, text = self.report()
        self.assertEqual(code, 0, text)
        self.assertIn("Failover (G3): 9 min 05 s from the fence of the old primary (06-3)", text)

    def test_a_failover_over_30_minutes_needs_attention(self):
        self.logs(31 * 60)
        code, text = self.report()
        self.assertEqual(code, 1)
        self.assertIn("failover took 31 min 00 s from 06-3-fence to 07-8-browser, over the goal of 30 min 00 s",
                      text.split("## Needs attention")[1])

    def test_a_run_without_both_steps_reports_no_failover_time(self):
        self.assertIsNone(acceptance.failover_time(self.run_directory))
        logs = self.run_directory / "logs"
        logs.mkdir(parents=True)
        (logs / "06-3-do-fence.log").write_text("# 2026-10-06T12:00:00+00:00 do fence\n")
        self.assertIsNone(acceptance.failover_time(self.run_directory))


class EvidenceTests(ToolTest):
    def test_evidence_holds_the_reports_and_the_end_of_the_last_attempt_of_each_key_log(self):
        logs = self.run_directory / "logs"
        logs.mkdir(parents=True)
        (self.run_directory / "REPORT.md").write_text("# Report\nNeeds attention: Nothing\n")
        (logs / "08-10-restore-notes-2.log").write_text(
            "** WARNING: connection is not using a post-quantum key exchange algorithm.\n"
            "2026-10-04 16:21:30.123456789 +0000 UTC m=+0.1 container exec_died abc\n"
            + "".join(f"line {number}\n" for number in range(30)) + "exit=0\n")
        (logs / "08-10-restore-notes.log").write_text("the first attempt\n")
        (logs / "99-unrelated.log").write_text("not evidence\n")
        self.assertEqual(self.tool("evidence")[0], 0)
        text = (self.run_directory / "EVIDENCE.md").read_text()
        self.assertIn("Needs attention: Nothing", text)
        self.assertIn("## run-record.md\n\n(missing)", text)
        self.assertIn("## logs/08-10-restore-notes-2.log (last 25 lines, 2 noise lines left out)", text)
        self.assertIn("line 29\nexit=0\n```", text)
        self.assertNotIn("line 5\n", text)
        self.assertNotIn("post-quantum", text)
        self.assertNotIn("the first attempt", text)
        self.assertNotIn("not evidence", text)

    def test_evidence_needs_a_run_folder_and_nothing_else(self):
        self.run_directory.mkdir()
        for arguments in (["--run", "run-2", "evidence"], ["--run", "run-1", "evidence", "full"]):
            with self.subTest(arguments), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                acceptance.main(arguments)


class GuideCommandTests(ToolTest):
    """The commands A4 needs so the agent guide becomes a command list."""

    def test_fence_checks_every_field(self):
        good = '{"ha": "not managed", "net0": "virtio=AA,firewall=1,link_down=1", "onboot": "0", "status": "stopped"}'
        self.assertEqual(self.tool("--step", "06-2", "do", "fence", "107", rules=[("fence 107", (0, good))])[0], 0)
        self.assertEqual(self.record()[-1]["values"]["status"], "stopped")
        link_up = good.replace("link_down=1", "link_down=0")
        self.assertEqual(self.tool("--step", "06-2", "do", "fence", "108", rules=[("fence 108", (0, link_up))])[0], 1)

    def test_ports_closed_from_the_client_and_from_a_vm(self):
        closed = [("CLOSED", (0, "192.168.0.102:22 timeout\nCLOSED: 192.168.0.102\n"))]
        code, fake = self.tool("--step", "06-3", "check", "ports-closed", "192.168.0.102", "client", rules=closed)
        self.assertEqual(code, 0)
        self.assertTrue(fake.calls[0].startswith("bash -c"))
        code, fake = self.tool("--step", "06-3", "check", "ports-closed", "192.168.0.102", "192.168.0.108", rules=closed)
        self.assertEqual(code, 0)
        self.assertIn("gunstein@192.168.0.108", fake.calls[0])
        opened = [("CLOSED", (1, "192.168.0.102:5432 open\nOPEN PORTS on 192.168.0.102\n"))]
        self.assertEqual(self.tool("--step", "06-3", "check", "ports-closed", "192.168.0.102", "client",
                                   rules=opened)[0], 1)

    def test_connect_proves_open_and_blocked(self):
        self.assertEqual(self.tool("--step", "05-5", "check", "connect", "client", "192.168.0.102", "22", "open",
                                   rules=[("/dev/tcp", (0, ""))])[0], 0)
        self.assertEqual(self.tool("--step", "05-5", "check", "connect", "192.168.0.108", "192.168.0.102", "5432",
                                   "blocked", rules=[("/dev/tcp", (124, ""))])[0], 0)
        self.assertEqual(self.record()[-1]["values"], {"outcome": "timed out"})
        self.assertEqual(self.tool("--step", "05-5", "check", "connect", "client", "192.168.0.102", "8443", "blocked",
                                   rules=[("/dev/tcp", (0, ""))])[0], 1)

    def test_quarantine_helper_ready_and_stop(self):
        ready = '{"exitcode": 0, "exited": 1, "out-data": "READY: host=todo-primary"}'
        self.assertEqual(self.tool("--step", "05-3", "check", "quarantine-ready", "107", "todo-primary",
                                   rules=[("app-quarantine.sh check todo-primary gunstein", (0, ready))])[0], 0)
        stopped = ('{"err-data": "WARNING: notes-postgres.service remains failed", "exitcode": 0, "exited": 1, '
                   '"out-data": "STOPPED: host=todo-primary"}')
        self.assertEqual(self.tool("--step", "09-3", "do", "quarantine-stop", "107", "todo-primary",
                                   rules=[("app-quarantine.sh stop", (0, stopped))])[0], 0)
        self.assertIn("remains failed", self.record()[-1]["values"]["warnings"])
        running = '{"exited": 0, "pid": 7}'
        self.assertEqual(self.tool("--step", "05-5", "do", "quarantine-stop", "107", "todo-primary",
                                   rules=[("app-quarantine.sh stop", (124, running))])[0], 1)

    def test_stopped_needs_every_service_down_and_no_containers(self):
        def answer(line):
            units = [unit for unit in acceptance.apps.services() if unit in line]
            return 0, "".join(f"{unit} failed 0 0\n" for unit in units) + "containers 0\n"
        self.assertEqual(self.tool("--step", "09-5", "check", "stopped", "192.168.0.102",
                                   rules=[("ActiveState", answer)])[0], 0)

        def one_running(line):
            code, out = answer(line)
            return code, out.replace("todo-app.service failed 0 0", "todo-app.service active 812 0")
        self.assertEqual(self.tool("--step", "09-5", "check", "stopped", "192.168.0.102",
                                   rules=[("ActiveState", one_running)])[0], 1)
        self.assertIn("FAIL: todo-app.service: active 812 0", self.log(self.record()[-1]))

    def test_link_power_and_onboot(self):
        down = '{"net0": "virtio=AA,firewall=1,link_down=1"}'
        self.assertEqual(self.tool("--step", "05-5", "do", "link", "107", "down", "192.168.0.102",
                                   rules=[("nic 107 link_down 1", (0, down))])[0], 0)
        up = '{"net0": "virtio=AA,firewall=1,link_down=0"}'
        self.assertEqual(self.tool("--step", "05-5", "do", "link", "107", "up", "192.168.0.102",
                                   rules=[("nic 107 link_down 0", (0, up)), ("boot_id", (0, "id\n"))])[0], 0)
        code, fake = self.tool("--step", "05-5", "do", "power", "107", "start", rules=[])
        self.assertEqual(code, 0)
        self.assertTrue(any("agent/ping" in call for call in fake.calls))
        self.assertEqual(self.tool("--step", "09-12", "do", "onboot", "107", "0",
                                   rules=[("get /nodes", (0, '{"onboot": 0}'))])[0], 0)
        self.assertEqual(self.tool("--step", "09-12", "do", "onboot", "107", "1",
                                   rules=[("get /nodes", (0, '{"onboot": 0}'))])[0], 1)

    def test_quarantine_profile_replaces_only_its_own_rules(self):
        state = {"rules": [{"pos": 0, "comment": "todo-quarantine-ssh-client"},
                           {"pos": 1, "comment": "todo-quarantine-replication"}], "posted": []}

        def answer(line):
            if "get /cluster/firewall/options" in line or "get /nodes/{node}/firewall/options" in line:
                return 0, '{"enable": 1}'
            if "firewall/options" in line and " get " in f" {line} ":
                return 0, '{"enable": 0, "policy_in": "DROP", "policy_out": "DROP"}'
            if " delete " in f" {line} ":
                return 0, "null"
            if " post " in f" {line} ":
                comment = line.split("comment=")[1].split()[0]
                enable = "0" if comment.endswith("replication") else "1"
                state["posted"].append({"pos": len(state["posted"]), "comment": comment, "enable": enable})
                return 0, "null"
            if "firewall/rules" in line:
                return 0, json.dumps(state["posted"] if state["posted"] else state["rules"])
            return 0, '{"net0": "virtio=AA,firewall=1"}'
        code, fake = self.tool("--step", "05-4", "do", "quarantine-profile", "107", "192.168.0.100", "192.168.0.108",
                               rules=[("pve_lab.py", answer)])
        self.assertEqual(code, 0, self.log(self.record()[-1]))
        deletes = [call for call in fake.calls if " delete " in f" {call} "]
        self.assertEqual([call.split("/rules/")[1].split()[0] for call in deletes], ["1", "0"])
        self.assertTrue(any("dest=192.168.0.108/32" in call and "todo-quarantine-replication" in call
                            and "enable=0" in call for call in fake.calls))

        foreign = [("get /cluster/firewall/options", (0, '{"enable": 1}')),
                   ("get /nodes/{node}/firewall/options", (0, '{"enable": 1}')),
                   ("firewall/options", (0, '{"enable": 0}')),
                   ("firewall/rules", (0, '[{"pos": 0, "comment": "someone else"}]'))]
        code, fake = self.tool("--step", "05-4", "do", "quarantine-profile", "108", "192.168.0.100", "192.168.0.102",
                               rules=foreign)
        self.assertEqual(code, 1)
        self.assertFalse(any(" delete " in f" {call} " or " post " in f" {call} " for call in fake.calls))

    def test_pin_ssh_verifies_the_host_key(self):
        rules = [("id_rsa.pub", (0, "ssh-rsa AAAA platform-ops-control\n")),
                 ("ssh_host_ed25519_key.pub", (0, "256 SHA256:abc root@todo-standby (ED25519)\n")),
                 ("ssh-keyscan", (0, "PINNED 192.168.0.108 SHA256:abc\n")),
                 ("hostname", (0, "todo-standby\n"))]
        self.assertEqual(self.tool("--step", "04-1", "do", "pin-ssh", "192.168.0.102", "192.168.0.108",
                                   rules=rules)[0], 0)
        self.assertEqual(self.record()[-1]["values"], {"fingerprint": "SHA256:abc", "hostname": "todo-standby"})
        mismatch = [rules[0], rules[1], ("ssh-keyscan", (1, "FINGERPRINT MISMATCH\n")), rules[3]]
        self.assertEqual(self.tool("--step", "09-7", "do", "pin-ssh", "192.168.0.108", "192.168.0.102",
                                   rules=mismatch)[0], 1)


class RemoteArgumentTests(ToolTest):
    def test_arguments_with_spaces_reach_the_remote_script_as_one_argument(self):
        """Run 13 appended only "ssh-rsa" to authorized_keys: ssh re-splits the remote command."""
        seen = []
        real = subprocess.run

        def remote_shell(argv, input=None, **kwargs):
            # What sshd does: hand the joined remote command to a shell.
            seen.append(real(["bash", "-c", argv[-1]], input='printf "%s|" "$@"', capture_output=True,
                             text=True).stdout)
            return subprocess.CompletedProcess(argv, 0, "", "")
        step = acceptance.Step(self.runs, "04-5", "do", "pin-ssh", [], "gunstein")
        with patch.object(acceptance.subprocess, "run", remote_shell), contextlib.redirect_stdout(io.StringIO()):
            step.ssh("192.168.0.108", "unused", "ssh-rsa AAAA platform-ops-control", "it's; $(x)")
        step.log_file.close()
        self.assertEqual(seen, ["ssh-rsa AAAA platform-ops-control|it's; $(x)|"])


class StreamingWaitTests(ToolTest):
    def test_replication_tls_waits_for_the_walreceiver_to_reconnect(self):
        """Run 14: pg_stat_replication was empty for a moment after the primary rebooted."""
        answers = iter([""] * 3 + ["x_standby|streaming|t|TLSv1.3\n"] * 3)
        with patch.object(acceptance, "STREAMING_TIMEOUT", 120):
            code, fake = self.tool("--step", "05-8-9b", "check", "replication-tls", "192.168.0.102",
                                   rules=[("pg_stat_ssl", lambda line: (0, next(answers)))])
        self.assertEqual(code, 0)
        self.assertEqual(sum("pg_stat_ssl" in call for call in fake.calls), 6)

    def test_replication_tls_still_fails_when_it_never_streams(self):
        with patch.object(acceptance, "STREAMING_TIMEOUT", 0):
            code, _ = self.tool("--step", "05-8-9b", "check", "replication-tls", "192.168.0.102",
                                rules=[("pg_stat_ssl", (0, ""))])
        self.assertEqual(code, 1)
        self.assertIn("no standby connection", self.log(self.record()[-1]))


class FakeShellTests(ToolTest):
    """The fake itself must catch what broke run 13 and similar shell mistakes."""

    def test_a_broken_remote_script_fails_the_test(self):
        step = acceptance.Step(self.runs, "00-1", "check", "x", [], "gunstein")
        self.addCleanup(step.log_file.close)
        with patch.object(acceptance.subprocess, "run", Fake([])), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(AssertionError, "script sent over ssh is not valid bash"):
                step.ssh("192.168.0.102", 'echo "unterminated')
            with self.assertRaisesRegex(AssertionError, "bash -c script is not valid bash"):
                acceptance.on(step, "client", "if true; then")

    def test_a_remote_command_that_does_not_split_as_meant_fails_the_test(self):
        fake = Fake([])
        with self.assertRaisesRegex(AssertionError, "does not start bash -s --"):
            fake(["ssh", "gunstein@192.168.0.102", "sh -c 'x'"])


if __name__ == "__main__":
    unittest.main()
