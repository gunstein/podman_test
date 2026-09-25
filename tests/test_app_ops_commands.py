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
sys.path.insert(0, str(ROOT / "deploy/ops"))
from app_ops import cli, inventory, recovery, standby, steps  # noqa: E402
from app_ops.transport import Host  # noqa: E402

NAMES = [entry["name"] for entry in steps.GROUP]


def setUpModule():
    # An operations-package-shaped root: the checkout's deploy/ plus rendered YAML.
    from tests.runtime_fixture import RUNTIME
    global PROJECT, _project
    _project = tempfile.TemporaryDirectory()
    PROJECT = Path(_project.name)
    (PROJECT / "deploy").symlink_to(ROOT / "deploy")
    (PROJECT / "generated").mkdir()
    (PROJECT / "generated/kube-runtime").symlink_to(RUNTIME)


def tearDownModule():
    _project.cleanup()


def spec(name, role, address, local=False):
    return inventory.HostSpec(name=name, role=role, address=address, user="ops", home=f"/home/{name}", local=local)


class World:
    """Every host answers healthily unless told otherwise; records (host, command) in order."""

    def __init__(self, firewall_rule=True, stream_failures=0, writable_standby=False):
        self.firewall_rule, self.stream_failures = firewall_rule, stream_failures
        self.writable_standby = writable_standby
        self.log = []

    def __call__(self, argv, input=None, capture_output=True, text=True):
        host = argv[-2].split("@")[1] if argv[0] == "ssh" else "controller"
        command = shlex.split(argv[-1]) if argv[0] == "ssh" else list(argv)
        if command[:3] == ["sudo", "-n", "--"]:
            command = command[3:]
        name = {"192.0.2.10": "todo-primary", "192.0.2.11": "todo-standby"}.get(host, host)
        step, out, rc = self.answer(name, command, input)
        self.log.append((name, step))
        return subprocess.CompletedProcess(argv, rc, out, "")

    def answer(self, host, command, stdin):
        if command[:1] == ["env"] and "app_installer" in command:
            sub = command[command.index("app_installer") + 1:]
            step = tuple(sub[:2]) if sub[0] == "replicate-workload" else (sub[0],)
            if sub[0] == "node-facts":
                return step, json.dumps({"host": host}), 0
            if sub[0] == "export-replication-secrets":
                return step, "U0VDUkVU\n", 0
            if sub[0] == "import-replication-secrets":
                return step + (stdin,), '{"changed": true}', 0
            if sub[:2] == ["replicate-workload", "streaming"] and self.stream_failures:
                self.stream_failures -= 1
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
            return ("firewall-rule", command[-1]), "", 0 if self.firewall_rule else 1
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
        self.assertEqual([step[0] for step in order[:5]], ["node-facts", "firewall-rule", "node-facts",
                                                         "check-standby-pair", "publish-primaries"])
        self.assertEqual(order[6], ("import-replication-secrets", "U0VDUkVU\n"))
        self.assertEqual([step for step in order if step[:2] == ("replicate-workload", "standby")],
                         [("replicate-workload", "standby", name) for name in NAMES])
        self.assertEqual([step for step in order[-3:]], [("replicate-workload", "streaming", name) for name in NAMES])
        rule = next(step for step in order if step[0] == "firewall-rule")[1]
        self.assertIn('source address="192.0.2.11/32" destination address="192.0.2.10" port port="5432-5434"', rule)

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
        self.assertLess(first[("publish-primaries",)], first[("reseed-group",)])
        self.assertLess(first[("reseed-group",)], first[("app_dr.py",)])
        self.assertEqual(kinds[-3:], [("replicate-workload", "streaming")] * 3)
        self.assertIn(("reseed-group",), world.steps("todo-primary"))

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


if __name__ == "__main__":
    unittest.main()
