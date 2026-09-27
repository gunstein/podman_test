"""app-ops failover: the steps in order, a safe rerun, and a stop that names the step."""
import json
import sys
import unittest
import unittest.mock

from tests import test_app_ops_commands as commands
from tests.test_app_ops_commands import World, spec

sys.path.insert(0, "deploy/ops")
from app_ops import cli, failover  # noqa: E402
from app_ops.transport import Host  # noqa: E402


def setUpModule():
    commands.setUpModule()


def tearDownModule():
    commands.tearDownModule()


LOGIN_FORM = '<form id="kc-form-login"><input id="username" name="username"></form>'


class FailoverWorld(World):
    """The promoted host runs locally, so the fake answers its identity; record state and failures are set."""

    def __init__(self, record=None, fail=None):
        super().__init__()
        self.record, self.fail = record, fail

    def answer(self, host, command, stdin):
        if command[0] == "hostname":
            return ("hostname",), "todo-standby\n", 0
        if command[0] == "ip":
            return ("ip",), "2: eth0 inet 192.0.2.11/24 scope global eth0\n", 0
        if command[:2] == ["sh", "-c"] and command[3] == "read-record":
            return ("read-record",), json.dumps({"state": self.record}) if self.record else "", 0
        if command[:2] == ["python3", failover.APP_DR]:
            return ("promote", command[4], command[6]), "", 1 if self.fail == "promote" else 0
        if command[:2] == ["bash", "-s"]:
            if self.fail == "ready":
                return ("wait-ready",), "waiting for service keycloak\nNOT READY after 300s: service keycloak\n", 1
            return ("wait-ready",), "READY", 0
        if command[:2] == ["bash", "-c"] and command[3] == "https":
            hostname, address, path, options = command[4], command[6], command[7], command[8:]
            if "openid-connect/auth" in path:
                refused = self.fail == "redirect" and "notes-frontend" in path
                return ("login-form", hostname, path), "" if refused else LOGIN_FORM, 22 if refused else 0
            if options == ["--head"]:
                sources = "'self'" if self.fail == "csp" else "'self' https://todo.test:8443"
                return ("csp", hostname), f"HTTP/1.1 200 OK\ncontent-security-policy: default-src 'self'; " \
                                          f"connect-src {sources}; frame-ancestors 'none'\n", 0
            return ("https", hostname, address), "ready", 0
        if command[:3] == ["podman", "exec", "nginx"]:
            return ("ca",), "sha256 Fingerprint=AA:BB\n", 0
        return super().answer(host, command, stdin)


class FailoverTests(unittest.TestCase):
    def run_failover(self, world, fenced="todo-primary is fenced", promotion="todo-standby", local=True):
        return failover.failover(
            str(commands.PROJECT), Host(cli.LOCAL, runner=world),
            Host(spec("todo-standby", "current_primary", "192.0.2.11", local=local), runner=world),
            Host(spec("todo-primary", "rebuild_standby", "192.0.2.10"), runner=world),
            fenced, promotion, say=lambda message: None)

    def kinds(self, world):
        wanted = ("read-record", "promote", "deploy-promoted", "require-promoted-group", "app_backup.py",
                  "wait-ready", "https", "login-form", "csp", "ca")
        return [step[0] for step in world.steps() if step[0] in wanted]

    def test_promotes_deploys_configures_backup_then_checks_services_and_login_page(self):
        world = FailoverWorld()
        report = self.run_failover(world)
        self.assertEqual(self.kinds(world), ["read-record", "promote", "deploy-promoted", "require-promoted-group",
                                             "app_backup.py", "wait-ready", "https", "https", "login-form", "csp",
                                             "login-form", "csp", "ca"])
        forms = [step for step in world.steps() if step[0] == "login-form"]
        self.assertEqual([step[1] for step in forms], ["todo.test", "todo.test"])
        self.assertIn("client_id=todo-frontend&redirect_uri=https%3A%2F%2Ftodo.test%3A8443%2F", forms[0][2])
        self.assertIn("client_id=notes-frontend&redirect_uri=https%3A%2F%2Fnotes.test%3A8443%2F", forms[1][2])
        self.assertEqual([step[1] for step in world.steps() if step[0] == "csp"], ["todo.test", "notes.test"])
        self.assertIn(("promote", "todo-primary is fenced", "todo-standby"), world.steps())
        self.assertEqual([step[1] for step in world.steps() if step[0] == "https"], ["todo.test", "notes.test"])
        self.assertTrue(all(step[2] == "192.0.2.11" for step in world.steps() if step[0] == "https"))
        self.assertTrue(report["changed"] and report["promoted_now"])
        self.assertEqual(report["users"]["ca_sha256"], "AA:BB")
        self.assertIn("todo.test and notes.test at 192.0.2.11", report["users"]["next"])
        self.assertIn("checks the login page, not a login", report["users"]["next"])

    def test_a_rerun_after_a_complete_promotion_skips_it(self):
        world = FailoverWorld(record="complete")
        report = self.run_failover(world)
        self.assertNotIn("promote", self.kinds(world))
        self.assertIn("wait-ready", self.kinds(world))
        self.assertFalse(report["promoted_now"])

    def test_a_failed_promotion_record_stops_before_anything_else(self):
        world = FailoverWorld(record="failed")
        with self.assertRaisesRegex(RuntimeError, 'step "promote".*"failed".*never retries'):
            self.run_failover(world)
        self.assertEqual(self.kinds(world), ["read-record"])

    def test_a_failure_names_its_step_and_stops_there(self):
        world = FailoverWorld(fail="promote")
        with self.assertRaisesRegex(RuntimeError, 'stopped at step "promote".*run failover again'):
            self.run_failover(world)
        self.assertNotIn("deploy-promoted", self.kinds(world))
        world = FailoverWorld(fail="ready")
        with self.assertRaisesRegex(RuntimeError, 'stopped at step "services": NOT READY after 300s: service keycloak'):
            self.run_failover(world)
        self.assertNotIn("ca", self.kinds(world))

    def test_a_refused_redirect_or_a_csp_without_keycloak_stops_at_the_login_page(self):
        """Code review: redirect URI and CSP errors block login while the services answer."""
        for fail, message in (("redirect", "notes: Keycloak refused the login request of client notes-frontend "
                                           "with redirect https://notes.test:8443/"),
                              ("csp", "notes: Content-Security-Policy connect-src 'self' does not allow "
                                      "https://todo.test:8443")):
            world = FailoverWorld(fail=fail)
            with self.subTest(fail=fail), self.assertRaisesRegex(RuntimeError, 'step "login-page": ' + message):
                self.run_failover(world)
            self.assertNotIn("ca", self.kinds(world))

    def test_wrong_confirmations_or_a_remote_host_change_nothing(self):
        for arguments in ({"fenced": "todo-primary"}, {"promotion": "todo-primary"}, {"local": False}):
            world = FailoverWorld()
            with self.subTest(**arguments), self.assertRaises(RuntimeError):
                self.run_failover(world, **arguments)
            self.assertEqual(world.steps(), [])


class FailoverCliTests(unittest.TestCase):
    def test_the_cli_starts_in_a_clean_process(self):
        # The hosts run python3 -m app_ops with only deploy/ops on the path.
        import subprocess
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-m", "app_ops", "failover", "--help"], cwd="/",
                                env={"PYTHONPATH": str(root / "deploy/ops"), "PATH": "/usr/bin:/bin"},
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--confirm-primary-fenced", result.stdout)

    def test_failover_passes_both_hosts_and_confirmations(self):
        from tests.test_app_ops_commands import CliDispatchTests
        helper = CliDispatchTests()
        helper.addCleanup = self.addCleanup
        with unittest.mock.patch.object(failover, "failover", return_value={"changed": True}) as called:
            code, printed, _ = helper.main(helper.RECOVERY, "failover", "--confirm-primary-fenced",
                                           "todo-primary is fenced", "--confirm-promotion", "todo-standby")
        self.assertEqual((code, json.loads(printed)), (0, {"changed": True}))
        self.assertEqual(helper.names(called.call_args), ["controller", "todo-standby", "todo-primary"])
        self.assertEqual(called.call_args.args[-2:], ("todo-primary is fenced", "todo-standby"))


if __name__ == "__main__":
    unittest.main()
