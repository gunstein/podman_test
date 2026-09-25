"""The SSH orchestrator: transport quoting and sudo, inventory, and install-quarantine-tool."""
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/ops"))
from todo_ops import cli, inventory, quarantine, transport  # noqa: E402

PRIMARY = inventory.HostSpec(name="todo-primary", role="primary", address="192.0.2.10",
                             user="ops", home="/home/ops")
POLICY = 'OTHER=1\nFILTER_RPC_ARGS="--allow-rpcs=guest-ping,guest-info"\n'


class FakeRunner:
    """Answers like a hardened host; records each command after unwrapping ssh and sudo."""

    def __init__(self, passwordless=False, fapolicyd="active", policy=POLICY, contexts="", booleans="",
                 trust="changed", install="changed", restorecon=""):
        self.passwordless, self.fapolicyd, self.policy = passwordless, fapolicyd, policy
        self.contexts, self.booleans, self.trust, self.install_result = contexts, booleans, trust, install
        self.restorecon = restorecon
        self.raw, self.commands = [], []

    def __call__(self, argv, input=None, capture_output=True, text=True):
        self.raw.append((argv, input))
        command = shlex.split(argv[-1]) if argv[0] == "ssh" else list(argv)
        if command[:4] == ["sudo", "-k", "-n", "true"]:
            return subprocess.CompletedProcess(argv, 0 if self.passwordless else 1, "", "")
        if command[0] == "sudo":
            command = command[command.index("--") + 1:]
        self.commands.append((argv[0] == "ssh", command))
        out, rc = "", 0
        if command == ["systemctl", "is-active", "fapolicyd"]:
            out, rc = self.fapolicyd + "\n", 0 if self.fapolicyd == "active" else 3
        elif command[:2] == ["/bin/sh", "-c"] and command[3:5] == ["trust-files", "trust"]:
            out = self.trust + "\n"
        elif command[:2] == ["/bin/sh", "-c"] and command[3:5] == ["trust-files", "install"]:
            out = self.install_result + "\n"
        elif command == ["cat", quarantine.GA_POLICY]:
            out = self.policy
        elif command == ["systemctl", "is-active", "qemu-guest-agent"]:
            out = "active\n"
        elif command == ["getenforce"]:
            out = "Enforcing\n"
        elif command[:3] == ["semanage", "fcontext", "-l"]:
            out = self.contexts
        elif command[:3] == ["semanage", "boolean", "-l"]:
            out = self.booleans
        elif command[0] == "restorecon":
            out = self.restorecon
        return subprocess.CompletedProcess(argv, rc, out, "")

    def kinds(self):
        return [("put" if command[4] == "install" else "trust") if command[:2] == ["/bin/sh", "-c"]
                else command[0] for _, command in self.commands]


class TransportTests(unittest.TestCase):
    def test_remote_commands_are_one_quoted_ssh_argument(self):
        runner = FakeRunner()
        transport.Host(PRIMARY, runner=runner).run(["sh", "-c", 'echo "$1"; rm -rf /', "x", "a b;c"])
        argv = runner.raw[0][0]
        self.assertEqual(argv[:1] + argv[-2:-1], ["ssh", "ops@192.0.2.10"])
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertEqual(shlex.split(argv[-1]), ["sh", "-c", 'echo "$1"; rm -rf /', "x", "a b;c"])

    def test_sudo_password_is_always_consumed_by_sudo_and_never_in_argv(self):
        runner = FakeRunner()
        host = transport.Host(PRIMARY, "s3cret pass", runner=runner)
        host.run(["cat", "/etc/x"], sudo=True, input="payload")
        argv, stdin = runner.raw[-1]
        self.assertEqual(shlex.split(argv[-1])[:6], ["sudo", "-k", "-S", "-p", "", "--"])
        self.assertEqual(stdin, "s3cret pass\npayload")
        self.assertFalse(any("s3cret" in part for argv, _ in runner.raw for part in argv))

    def test_passwordless_sudo_never_sends_the_password(self):
        runner = FakeRunner(passwordless=True)
        transport.Host(PRIMARY, "s3cret", runner=runner).run(["id"], sudo=True, input="payload")
        self.assertEqual(runner.raw[-1][1], "payload")
        self.assertEqual(shlex.split(runner.raw[-1][0][-1])[:3], ["sudo", "-n", "--"])

    def test_missing_password_and_failures_never_echo_stdin(self):
        with self.assertRaisesRegex(transport.CommandError, "--ask-become-pass"):
            transport.Host(PRIMARY, runner=FakeRunner()).run(["id"], sudo=True)

        def failing(argv, input=None, **kwargs):
            return subprocess.CompletedProcess(argv, 5, "", "boom")
        with self.assertRaises(transport.CommandError) as error:
            transport.Host(PRIMARY, runner=failing).run(["podman", "secret", "create", "x", "-"], input="value")
        self.assertIn("exit 5", str(error.exception))
        self.assertNotIn("value", str(error.exception))

    def test_local_hosts_run_without_ssh(self):
        runner = FakeRunner()
        spec = inventory.HostSpec(**{**PRIMARY.__dict__, "local": True})
        transport.Host(spec, runner=runner).run(["hostname"])
        self.assertEqual(runner.raw[0][0], ["hostname"])


class InventoryTests(unittest.TestCase):
    def load(self, text, roles=("primary", "standby")):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as file:
            file.write(text)
        self.addCleanup(Path(file.name).unlink)
        return inventory.load(file.name, roles)

    def test_valid_initial_inventory(self):
        hosts = self.load("user: ops\nhosts:\n"
                          "  todo-primary: {role: primary, address: 192.0.2.10, local: true}\n"
                          "  todo-standby: {role: standby, address: 192.0.2.11}\n")
        self.assertTrue(hosts["primary"].local)
        self.assertEqual(hosts["standby"].destination, "ops@192.0.2.11")
        self.assertEqual(hosts["standby"].home, "/home/ops")

    def test_refuses_missing_or_duplicate_roles_names_and_addresses(self):
        for text in ("user: ops\nhosts:\n  a: {role: primary, address: 192.0.2.10}\n",
                     "user: ops\nhosts:\n  a: {role: primary, address: 192.0.2.10}\n"
                     "  b: {role: primary, address: 192.0.2.11}\n",
                     "user: ops\nhosts:\n  a: {role: primary, address: 192.0.2.10}\n"
                     "  b: {role: standby, address: 192.0.2.10}\n",
                     "user: ops\nhosts:\n  a: {role: primary, address: vm1.example}\n"
                     "  b: {role: standby, address: 192.0.2.11}\n",
                     "user: 'ops; rm'\nhosts:\n  a: {role: primary, address: 192.0.2.10}\n"
                     "  b: {role: standby, address: 192.0.2.11}\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load(text)


class QuarantineToolTests(unittest.TestCase):
    def install(self, runner, **options):
        controller = transport.Host(cli.LOCAL, "pw", runner=runner)
        primary = transport.Host(PRIMARY, "pw", runner=runner)
        return quarantine.install(str(ROOT), controller, primary, **options)

    def test_guest_agent_policy_keeps_existing_rpcs_once(self):
        updated = quarantine.guest_agent_policy(POLICY)
        self.assertIn('FILTER_RPC_ARGS="--allow-rpcs=guest-ping,guest-info,guest-exec,guest-exec-status"', updated)
        self.assertIn("OTHER=1", updated)
        self.assertEqual(quarantine.guest_agent_policy(updated), updated)
        for policy in ("OTHER=1\n", POLICY + ' FILTER_RPC_ARGS="x"\n', 'FILTER_RPC_ARGS="--allow-rpcs=A B"\n'):
            with self.subTest(policy=policy), self.assertRaises(RuntimeError):
                quarantine.guest_agent_policy(policy)

    def test_helper_context_accepts_only_the_expected_type(self):
        good = f"{quarantine.HELPER_REGEX}    all files    system_u:object_r:{quarantine.HELPER_TYPE}:s0\n"
        self.assertEqual(len(quarantine.helper_contexts(good + "/other  all files  x\n")), 1)
        with self.assertRaises(RuntimeError):
            quarantine.helper_contexts(good.replace(quarantine.HELPER_TYPE, "bin_t"))

    def test_hardened_install_trusts_sources_before_installing_then_trusts_targets(self):
        runner = FakeRunner()
        self.assertTrue(self.install(runner))
        kinds = runner.kinds()
        # Installer staging (controller trust, installs, target trust), then the helper the same way.
        modules = len(list((ROOT / "deploy/installer/app_installer").glob("*.py")))
        # install is the root-owned directory, put one trust-files install.
        self.assertEqual([kind for kind in kinds if kind in ("trust", "install", "put")],
                         ["trust", "install"] + ["put"] * modules + ["trust", "trust", "install", "put", "trust"])
        controller_trust = [command for remote, command in runner.commands if not remote and command[4:5] == ["trust"]]
        self.assertTrue(all(argument.startswith(str(ROOT)) for command in controller_trust for argument in command[6:]))
        self.assertEqual(kinds[-1], "restorecon")
        self.assertNotIn("semanage", kinds)
        self.assertNotIn("cat", kinds)

    def test_optional_guest_exec_and_selinux_steps_only_when_asked(self):
        runner = FakeRunner()
        self.install(runner, guest_exec=True, selinux_entrypoint=True)
        commands = [command for _, command in runner.commands]
        self.assertIn(["systemctl", "restart", "qemu-guest-agent"], commands)
        self.assertIn(["semanage", "fcontext", "-a", "-t", quarantine.HELPER_TYPE, quarantine.HELPER_REGEX], commands)
        self.assertIn(["setsebool", "-P", "virt_qemu_ga_run_unconfined", "on"], commands)
        policy = next(command for command in commands if command[:2] == ["sh", "-c"] and "policy" in command)
        self.assertTrue(policy[-1].endswith("~"))

    def test_repeat_on_a_configured_host_reports_no_change(self):
        runner = FakeRunner(trust="unchanged", install="unchanged",
                            policy=quarantine.guest_agent_policy(POLICY),
                            contexts=f"{quarantine.HELPER_REGEX}  all files  u:object_r:{quarantine.HELPER_TYPE}:s0\n",
                            booleans="virt_qemu_ga_run_unconfined (on   ,   on)  Allow\n")
        self.assertFalse(self.install(runner, guest_exec=True, selinux_entrypoint=True))

    def test_inactive_fapolicyd_target_gets_plain_staging_but_no_helper(self):
        runner = FakeRunner(fapolicyd="inactive")
        with self.assertRaisesRegex(RuntimeError, "fapolicyd is not active"):
            self.install(runner)
        self.assertNotIn("put", runner.kinds())


if __name__ == "__main__":
    unittest.main()
