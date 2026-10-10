"""app_ops DR commands against a fake two-host world: order, gates and what crosses hosts."""
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/dr"))
from app_ops import cli, inventory, recovery, standby, steps  # noqa: E402
from app_ops.transport import Host  # noqa: E402

NAMES = [database.name for database in steps.GROUP]
# The public hostnames each host recorded (app_dr_host target-values).
RECORDED = {"todo-primary": {"TARGET_IDENTITY_HOSTNAME": "auth.test", "TARGET_TODO_HOSTNAME": "shop.example.org",
                             "TARGET_NOTES_HOSTNAME": "notes.test"},
            "todo-standby": {"TARGET_IDENTITY_HOSTNAME": "auth.test", "TARGET_TODO_HOSTNAME": "todo.test",
                             "TARGET_NOTES_HOSTNAME": "notes.test"}}


def setUpModule():
    # An operations-package-shaped root: the checkout's deploy/ plus rendered YAML.
    from tests.runtime_fixture import RUNTIME
    global PROJECT, _project
    _project = tempfile.TemporaryDirectory()
    PROJECT = Path(_project.name)
    (PROJECT / "deploy").symlink_to(ROOT / "deploy")
    (PROJECT / "generated").mkdir()
    (PROJECT / "generated/kube-runtime").symlink_to(RUNTIME)
    # bundle.json and generated/target, as build-operations-package.sh adds them.
    from app_installer import bundle
    bundle.build(ROOT, ROOT / "deploy/environments/prod/values.yaml", PROJECT)


def tearDownModule():
    _project.cleanup()


def spec(name, role, address, local=False):
    return inventory.HostSpec(name=name, role=role, address=address, user="ops", home=f"/home/{name}", local=local)


class World:
    """Every host answers healthily unless told otherwise; records (host, command) in order."""

    def __init__(self, firewall_rule=True, stream_failures=0, writable_standby=False,
                 rule_in=("running", "permanent"), zone="public", rule_zone="public", blocked_path=False,
                 failing=()):
        self.firewall_rule, self.stream_failures = firewall_rule, stream_failures
        self.failing = set(failing)  # app_dr_host steps that fail, such as ("standby-reseed-check",)
        self.blocked_path = blocked_path
        self.rule_in, self.zone, self.rule_zone = rule_in, zone, rule_zone
        self.writable_standby = writable_standby
        self.log, self.commands = [], []

    def __call__(self, argv, input=None, capture_output=True, text=True, timeout=None):
        host = argv[-2].split("@")[1] if argv[0] == "ssh" else "controller"
        command = shlex.split(argv[-1]) if argv[0] == "ssh" else list(argv)
        if command[:3] == ["sudo", "-n", "--"]:
            command = command[3:]
        name = {"192.0.2.10": "todo-primary", "192.0.2.11": "todo-standby"}.get(host, host)
        step, out, rc = self.answer(name, command, input)
        self.log.append((name, step))
        self.commands.append((name, command))
        return subprocess.CompletedProcess(argv, rc, out, "")

    def answer(self, host, command, stdin):
        if command[:1] == ["env"] and "app_dr_host" in command:
            sub = command[command.index("app_dr_host") + 1:]
            step = tuple(sub[:2]) if sub[0] == "replicate-workload" else (sub[0],)
            if step in self.failing:
                return step, "", 1
            if sub[:2] == ["replicate-workload", "slot"]:
                return step, json.dumps({"changed": False, "slot": sub[sub.index("--app") + 1] + "_rebuilt_standby"}), 0
            if sub[0] == "node-facts":
                return step, json.dumps({"host": host}), 0
            if sub[0] == "target-values":
                return step, json.dumps({"changed": False, "values": RECORDED[host]}), 0
            if sub[0] == "export-replication-secrets":
                return step, "U0VDUkVU\n", 0
            if sub[0] == "import-replication-secrets":
                return step + (stdin,), '{"changed": true}', 0
            if sub[:2] == ["replicate-workload", "streaming"] and self.stream_failures:
                self.stream_failures -= 1
                return step, "", 1
            if sub[:2] == ["replicate-workload", "replication-path"] and self.blocked_path:
                return step, "", 1
            if sub[:2] == ["replicate-workload", "status"]:
                writable = self.writable_standby
                return step, json.dumps({"changed": False, "status": {"in_recovery": not writable,
                                                                       "transaction_read_only": not writable}}), 0
            if sub[0] == "cluster-status":
                return step, json.dumps({"changed": False, "status": {"role": sub[1]}}), 0
            return step + tuple(argument for argument in sub[1:] if argument in NAMES), '{"changed": false}', 0
        if command[:2] == ["systemctl", "is-active"]:
            return ("fapolicyd",), "active\n", 0
        if command[:2] == ["/bin/sh", "-c"]:
            return ("trust-files", command[4]), "unchanged\n", 0
        if command[0] == "firewall-cmd":
            if command[1].startswith("--state"):
                return ("firewall-state",), "", 0
            if command[1] == "--get-zone-of-interface=eth0":
                return ("firewall-zone",), self.zone + "\n", 0
            where = "permanent" if "--permanent" in command else "running"
            present = self.firewall_rule and where in self.rule_in and f"--zone={self.rule_zone}" in command
            return ("firewall-rule", command[-1], where), "", 0 if present else 1
        if command[0] == "hostname":
            return ("hostname",), host + "\n", 0
        if command[0] == "ip":
            address = {"todo-primary": "192.0.2.10", "todo-standby": "192.0.2.11"}.get(host, "127.0.0.1")
            return ("ip",), f"2: eth0 inet {address}/24 scope global eth0\n", 0
        if command[0] == "env" and command[-1] == "configure" or (command[0] == "env" and "configure" in command):
            return (Path(command[3]).name, "configure"), '{"changed": false}', 0
        return (command[0],), "", 0

    def steps(self, host=None):
        return [step for name, step in self.log if host in (None, name)]

    def option(self, host, operation, name):
        """The value of option name in every app_dr_host operation (a tuple) run on host."""
        return [command[command.index(name) + 1] for where, command in self.commands
                if where == host and "app_dr_host" in command
                and tuple(command[command.index("app_dr_host") + 1:][:len(operation)]) == operation]


class InitialTopologyTests(unittest.TestCase):
    def hosts(self, world):
        return (Host(cli.LOCAL, runner=world), Host(spec("todo-primary", "primary", "192.0.2.10"), runner=world),
                Host(spec("todo-standby", "standby", "192.0.2.11"), runner=world))

    def test_bootstrap_runs_every_gate_before_publishing_then_transfers_secrets_by_stdin(self):
        world = World()
        standby.bootstrap(str(PROJECT), *self.hosts(world))
        order = [step for step in world.steps() if step[0] in (
            "node-facts", "firewall-rule", "check-standby-pair", "publish-primaries", "export-replication-secrets",
            "import-replication-secrets", "replicate-workload")]
        self.assertEqual([step[0] for step in order[:6]], ["node-facts", "firewall-rule", "firewall-rule",
                                                         "node-facts", "check-standby-pair", "publish-primaries"])
        self.assertEqual([step[2] for step in order[1:3]], ["running", "permanent"])
        self.assertEqual(order[7], ("import-replication-secrets", "U0VDUkVU\n"))
        self.assertEqual([step for step in order if step[:2] == ("replicate-workload", "standby")],
                         [("replicate-workload", "standby", name) for name in NAMES])
        self.assertEqual([step for step in order[-3:]], [("replicate-workload", "streaming", name) for name in NAMES])
        rule = next(step for step in order if step[0] == "firewall-rule")[1]
        self.assertIn('source address="192.0.2.11/32" destination address="192.0.2.10" port port="5432-5434"', rule)

    def test_the_standby_gets_the_primarys_public_hostnames_and_its_own_address(self):
        world = World()
        standby.bootstrap(str(PROJECT), *self.hosts(world))
        operation = ("replicate-workload", "standby")
        self.assertEqual([json.loads(v) for v in world.option("todo-standby", operation, "--target-values")],
                         [RECORDED["todo-primary"]] * len(NAMES))
        self.assertEqual(world.option("todo-standby", operation, "--node-address"), ["192.0.2.11"] * len(NAMES))
        # Both hosts get the package's rendered files, never Quadlet templates.
        staged = [command[-1] for _host, command in world.commands if command[:2] == ["sh", "-c"]]
        self.assertTrue(any(path.endswith("/bundle.json") for path in staged))
        self.assertFalse([path for path in staged if path.endswith(".j2")])

    def test_secret_values_only_travel_on_stdin(self):
        world = World()
        standby.sync_secrets(str(PROJECT), *self.hosts(world))
        # The fake transfer never appears as an argument anywhere.
        self.assertFalse([step for step in world.steps() if "U0VDUkVU" in " ".join(map(str, step[:1]))])
        self.assertIn(("import-replication-secrets", "U0VDUkVU\n"), world.steps("todo-standby"))

    def test_missing_firewall_rule_stops_before_anything_is_published(self):
        world = World(firewall_rule=False)
        with self.assertRaisesRegex(RuntimeError, "add-rich-rule="):
            standby.bootstrap(str(PROJECT), *self.hosts(world))
        self.assertFalse([step for step in world.steps() if step[0] in ("check-standby-pair", "publish-primaries")])

    def test_the_firewall_rule_must_apply_now_and_after_a_reload(self):
        # Checked in the zone of the primary's interface, running and permanent.
        standby.require_firewall(*self.hosts(World(zone="internal", rule_zone="internal"))[1:])
        for world, message in ((World(rule_in=("permanent",)), "running configuration"),
                               (World(rule_in=("running",)), "permanent configuration"),
                               (World(zone="internal", rule_zone="public"), "zone internal of eth0")):
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                standby.bootstrap(str(PROJECT), *self.hosts(world))
            self.assertFalse([step for step in world.steps() if step[0] == "publish-primaries"])

    def test_streaming_is_retried_and_a_writable_standby_fails_status(self):
        world = World(stream_failures=2)
        original = steps.retry
        steps.retry = lambda action, attempts, delay, sleep=None: original(action, attempts, delay, lambda _: None)
        try:
            standby.replication_status(str(PROJECT), *self.hosts(world))
            with self.assertRaisesRegex(RuntimeError, "writable"):
                standby.replication_status(str(PROJECT), *self.hosts(World(writable_standby=True)))
        finally:
            steps.retry = original
        self.assertEqual(len([s for s in world.steps() if s[:2] == ("replicate-workload", "streaming")]), 5)


class RecoveryTests(unittest.TestCase):
    def hosts(self, world, current_local=False):
        return (Host(cli.LOCAL, runner=world),
                Host(spec("todo-standby", "current_primary", "192.0.2.11", local=current_local), runner=world),
                Host(spec("todo-primary", "rebuild_standby", "192.0.2.10"), runner=world))

    def test_rebuild_gates_both_hosts_then_publishes_then_reseeds_then_verifies(self):
        world = World()
        recovery.rebuild(str(PROJECT), *self.hosts(world), "todo-primary is fenced", "todo-primary")
        order = [step for step in world.steps() if step[0] in ("true", "replicate-workload", "publish-primaries",
                                                               "reseed-group", "app_dr.py")]
        kinds = [step[:2] if step[0] == "replicate-workload" else step[:1] for step in order]
        first = {kind: kinds.index(kind) for kind in reversed(kinds)}
        self.assertEqual(kinds[:2], [("true",), ("true",)])
        self.assertLess(first[("replicate-workload", "rebuild-primary-check")],
                        first[("replicate-workload", "quarantined")])
        self.assertLess(first[("replicate-workload", "quarantined")], first[("replicate-workload", "reseed-check")])
        self.assertLess(max(i for i, k in enumerate(kinds) if k == ("replicate-workload", "reseed-check")),
                        first[("publish-primaries",)])
        self.assertLess(first[("publish-primaries",)], first[("replicate-workload", "replication-path")])
        # The rebuild host gets the replication CA the primary may just have created.
        steps_in_order = [step[0] for step in world.steps()]
        self.assertLess(steps_in_order.index("publish-primaries"), steps_in_order.index("export-replication-secrets"))
        self.assertLess(steps_in_order.index("import-replication-secrets"), steps_in_order.index("reseed-group"))
        self.assertIn(("export-replication-secrets",), world.steps("todo-standby"))
        self.assertLess(max(i for i, k in enumerate(kinds) if k == ("replicate-workload", "replication-path")),
                        first[("reseed-group",)])
        self.assertEqual([step for step in world.steps("todo-primary") if step[:2] == ("replicate-workload",
                                                                                      "replication-path")],
                         [("replicate-workload", "replication-path", name) for name in NAMES])
        self.assertLess(first[("publish-primaries",)], first[("reseed-group",)])
        self.assertLess(first[("reseed-group",)], first[("app_dr.py",)])
        self.assertEqual(kinds[-3:], [("replicate-workload", "streaming")] * 3)
        self.assertIn(("reseed-group",), world.steps("todo-primary"))
        # The rebuilt host serves the current primary's public hostnames, on its own address.
        for operation in (("replicate-workload", "reseed-check"), ("reseed-group",)):
            values = world.option("todo-primary", operation, "--target-values")
            self.assertTrue(values)
            self.assertEqual({json.dumps(json.loads(v), sort_keys=True) for v in values},
                             {json.dumps(RECORDED["todo-standby"], sort_keys=True)})
            self.assertEqual(set(world.option("todo-primary", operation, "--node-address")), {"192.0.2.10"})

    def test_a_blocked_replication_path_stops_the_rebuild_before_the_reseed(self):
        world = World(blocked_path=True)
        with self.assertRaises(RuntimeError):
            recovery.rebuild(str(PROJECT), *self.hosts(world), "todo-primary is fenced", "todo-primary")
        self.assertIn(("publish-primaries",), world.steps())
        self.assertFalse([step for step in world.steps() if step[0] == "reseed-group"])

    def test_the_preflight_stays_read_only_and_does_not_probe_the_path(self):
        world = World(blocked_path=True)
        recovery.preflight_rebuild(str(PROJECT), *self.hosts(world), "todo-primary is fenced", "todo-primary")
        self.assertFalse([step for step in world.steps()
                          if step[0] == "publish-primaries" or step[:2] == ("replicate-workload",
                                                                            "replication-path")])

    def test_wrong_confirmations_refuse_before_any_rebuild_host_change(self):
        world = World()
        with self.assertRaisesRegex(RuntimeError, "exact confirmations"):
            recovery.rebuild(str(PROJECT), *self.hosts(world), "todo-primary", "todo-primary")
        self.assertFalse([step for step in world.steps() if step[0] in ("reseed-group", "publish-primaries", "sh")])

    def test_promoted_deployment_runs_only_on_the_local_promoted_host(self):
        with self.assertRaisesRegex(RuntimeError, "local: true"):
            recovery.deploy_promoted(str(PROJECT), *self.hosts(World())[:2])
        world = World()
        recovery.deploy_promoted(str(PROJECT), *self.hosts(world, current_local=True)[:2])
        self.assertIn(("deploy-promoted",), world.steps("controller"))

    def test_cluster_status_is_read_only(self):
        world = World()
        report = recovery.cluster_status(*self.hosts(world)[1:])
        self.assertEqual(report, {"changed": False, "primary": {"role": "primary"}, "standby": {"role": "standby"}})
        self.assertFalse([step for step in world.steps() if step[0] in ("sh", "trust-files", "install")])


class ReseedStandbyTests(unittest.TestCase):
    """reseed-standby (D10): every check before the erase; the primary only loses idle slots."""

    def hosts(self, world):
        return (Host(cli.LOCAL, runner=world), Host(spec("todo-standby", "primary", "192.0.2.11"), runner=world),
                Host(spec("todo-primary", "standby", "192.0.2.10"), runner=world))

    def test_the_standby_proves_it_is_one_then_is_erased_then_copied_again_with_its_slot(self):
        world = World()
        self.assertTrue(recovery.reseed_standby(str(PROJECT), *self.hosts(world), "todo-primary"))
        order = [step[0] if step[0] != "replicate-workload" else step[1] for step in world.steps()
                 if step[0] in ("replicate-workload", "standby-reseed-check", "erase-standby",
                                "import-replication-secrets", "firewall-rule", "app_dr.py")]
        first = {kind: order.index(kind) for kind in reversed(order)}
        last = {kind: i for i, kind in enumerate(order)}
        self.assertLess(last["firewall-rule"], first["slot"])
        self.assertLess(last["slot"], first["import-replication-secrets"])
        self.assertLess(first["import-replication-secrets"], first["standby-reseed-check"])
        self.assertLess(first["standby-reseed-check"], first["erase-standby"])
        self.assertLess(first["erase-standby"], first["drop-slot"])
        self.assertLess(last["drop-slot"], first["standby"])
        self.assertLess(last["standby"], first["app_dr.py"])
        self.assertEqual(order[-3:], ["streaming"] * 3)
        # The erase and the copies run on the standby; the slots are found and dropped on the primary.
        self.assertIn(("erase-standby",), world.steps("todo-primary"))
        self.assertEqual([step for step in world.steps("todo-standby") if step[:2] == ("replicate-workload", "drop-slot")],
                         [("replicate-workload", "drop-slot", name) for name in NAMES])
        for operation in (("replicate-workload", "drop-slot"), ("replicate-workload", "standby"),
                          ("replicate-workload", "streaming")):
            host = "todo-primary" if operation[1] == "standby" else "todo-standby"
            self.assertEqual(world.option(host, operation, "--slot"), [name + "_rebuilt_standby" for name in NAMES])
        self.assertEqual(set(world.option("todo-primary", ("erase-standby",), "--confirm-reseed")), {"todo-primary"})
        self.assertEqual(set(world.option("todo-primary", ("replicate-workload", "standby"), "--node-address")),
                         {"192.0.2.10"})

    def test_a_wrong_confirmation_or_a_failed_check_erases_nothing(self):
        world = World()
        with self.assertRaisesRegex(RuntimeError, "must name the standby exactly"):
            recovery.reseed_standby(str(PROJECT), *self.hosts(world), "todo-standby")
        self.assertEqual(world.steps(), [])
        for failing in ("standby-reseed-check",), ("replicate-workload", "slot"):
            with self.subTest(failing=failing):
                world = World(failing=[failing])
                with self.assertRaises(RuntimeError):
                    recovery.reseed_standby(str(PROJECT), *self.hosts(world), "todo-primary")
                self.assertFalse([step for step in world.steps()
                                  if step[0] == "erase-standby" or step[1:2] == ("drop-slot",)])
        world = World(firewall_rule=False)
        with self.assertRaisesRegex(RuntimeError, "rich rule"):
            recovery.reseed_standby(str(PROJECT), *self.hosts(world), "todo-primary")
        self.assertFalse([step for step in world.steps() if step[0] == "erase-standby"])


class CliTests(unittest.TestCase):
    def test_each_command_requires_its_topology(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as file:
            file.write("user: ops\nhosts:\n  todo-primary: {role: primary, address: 192.0.2.10}\n"
                       "  todo-standby: {role: standby, address: 192.0.2.11}\n")
        self.addCleanup(Path(file.name).unlink)
        with unittest.mock.patch("sys.stderr"):
            self.assertEqual(cli.main(["--inventory", file.name, "cluster-status"]), 1)
            self.assertEqual(cli.main(["--inventory", file.name, "rebuild-standby", "--confirm-fenced", "x",
                                       "--confirm-reseed", "y"]), 1)



class CliDispatchTests(unittest.TestCase):
    """Every command reaches its function with the hosts of its roles, and prints one JSON line."""

    INITIAL = ("user: ops\nhosts:\n  todo-primary: {role: primary, address: 192.0.2.10, local: true}\n"
               "  todo-standby: {role: standby, address: 192.0.2.11}\n")
    RECOVERY = ("user: ops\nhosts:\n  todo-standby: {role: current_primary, address: 192.0.2.11, local: true}\n"
                "  todo-primary: {role: rebuild_standby, address: 192.0.2.10}\n")

    def inventory(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as file:
            file.write(text)
        self.addCleanup(Path(file.name).unlink)
        return file.name

    def main(self, text, *argv):
        with unittest.mock.patch("sys.stdout") as stdout, unittest.mock.patch("sys.stderr") as stderr:
            code = cli.main(["--inventory", self.inventory(text), *argv])
        printed = "".join(call.args[0] for call in stdout.write.call_args_list)
        errors = "".join(call.args[0] for call in stderr.write.call_args_list)
        return code, printed, errors

    def names(self, call):
        return [argument.name for argument in call.args if isinstance(argument, Host)]

    def test_each_command_calls_its_function_with_the_right_hosts(self):
        both, controller = ["todo-primary", "todo-standby"], ["controller"]
        cases = [
            ("preflight-standby", standby, "preflight", self.INITIAL, controller + both),
            ("sync-standby-secrets", standby, "sync_secrets", self.INITIAL, controller + both),
            ("bootstrap-standby", standby, "bootstrap", self.INITIAL, controller + both),
            ("replication-status", standby, "replication_status", self.INITIAL, controller + both),
            ("install-dr-tool", standby, "install_dr_tools", self.INITIAL, controller + both),
            ("reseed-standby", recovery, "reseed_standby", self.INITIAL, controller + both),
            ("install-quarantine-tool", cli.quarantine, "install", self.INITIAL, controller + ["todo-primary"]),
            ("deploy-promoted-application", recovery, "deploy_promoted", self.RECOVERY,
             controller + ["todo-standby"]),
            ("configure-backup", recovery, "configure_backup", self.RECOVERY, controller + ["todo-standby"]),
            # Read-only: no controller, current primary first.
            ("cluster-status", recovery, "cluster_status", self.RECOVERY, ["todo-standby", "todo-primary"]),
        ]
        for command, module, function, text, hosts in cases:
            with self.subTest(command=command), unittest.mock.patch.object(
                    module, function, return_value=True) as called:
                confirmation = ["--confirm-reseed", "todo-standby"] if command == "reseed-standby" else []
                code, printed, _ = self.main(text, command, *confirmation)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(printed), {"changed": True})
                self.assertEqual(called.call_count, 1)
                self.assertEqual(self.names(called.call_args), hosts)

    def test_install_dr_tool_installs_the_same_settings_and_check_on_both_hosts(self):
        with unittest.mock.patch.object(standby, "install_dr_tool", return_value=False) as called:
            self.main(self.INITIAL, "install-dr-tool")
        self.assertEqual([call.args[2].name for call in called.call_args_list], ["todo-standby", "todo-primary"])
        for call in called.call_args_list:
            self.assertEqual(call.args[3].address, "192.0.2.10")
            self.assertEqual(call.args[4], "todo-standby")

    def test_quarantine_options_are_passed_through(self):
        with unittest.mock.patch.object(cli.quarantine, "install", return_value=False) as called:
            self.main(self.INITIAL, "install-quarantine-tool", "--enable-guest-exec")
        self.assertEqual(called.call_args.kwargs, {"guest_exec": True, "selinux_entrypoint": False})

    def test_rebuild_commands_pass_both_confirmations(self):
        for command, function, printed_result in (("preflight-standby-rebuild", "preflight_rebuild", False),
                                                  ("rebuild-standby", "rebuild", True)):
            with self.subTest(command=command), unittest.mock.patch.object(
                    recovery, function, return_value=True) as called:
                code, printed, _ = self.main(self.RECOVERY, command, "--confirm-fenced", "todo-primary is fenced",
                                             "--confirm-reseed", "todo-primary")
                self.assertEqual(code, 0)
                self.assertEqual(called.call_args.args[-2:], ("todo-primary is fenced", "todo-primary"))
                self.assertEqual(json.loads(printed), {"changed": printed_result})

    def test_a_report_is_printed_as_it_is(self):
        report = {"changed": False, "primary": {}, "standby": {}}
        with unittest.mock.patch.object(recovery, "cluster_status", return_value=report):
            self.assertEqual(json.loads(self.main(self.RECOVERY, "cluster-status")[1]), report)

    def test_failures_print_one_error_line_and_exit_1(self):
        with unittest.mock.patch.object(standby, "bootstrap", side_effect=RuntimeError("firewall rule missing")):
            code, printed, errors = self.main(self.INITIAL, "bootstrap-standby")
        self.assertEqual((code, printed, errors), (1, "", "app-ops: firewall rule missing\n"))
        with unittest.mock.patch("sys.stderr") as stderr:
            self.assertEqual(cli.main(["--inventory", "/nonexistent.yaml", "cluster-status"]), 1)
        self.assertIn("app-ops:", "".join(call.args[0] for call in stderr.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
