"""The scheduled checks and backups (M1, M2): their units, and how app-ops installs them."""
import configparser
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/dr"))
from app_installer import settings  # noqa: E402
from app_ops import inventory, recovery, standby, steps  # noqa: E402
from app_ops.transport import Host  # noqa: E402

UNITS = ROOT / "deploy/dr/systemd"


def unit(name):
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(UNITS / name)
    return parser


class UnitTests(unittest.TestCase):
    def test_each_service_runs_one_installed_tool_once(self):
        for name, command in (("todo-dr-check", "app_dr.py check"),
                              ("todo-backup", "app_backup.py nightly --keep-days 7")):
            with self.subTest(name=name):
                service = unit(f"{name}.service")
                self.assertEqual(service["Service"]["Type"], "oneshot")
                self.assertEqual(service["Service"]["ExecStart"],
                                 f"/usr/bin/python3 {settings.TOOLS_BIN}/{command}")
                timer = unit(f"{name}.timer")
                self.assertEqual(timer["Install"]["WantedBy"], "timers.target")
                self.assertNotIn("Unit", timer["Timer"])  # the timer starts the service of its own name

    def test_the_check_runs_every_quarter_hour_and_the_backup_every_night(self):
        self.assertEqual(unit("todo-dr-check.timer")["Timer"]["OnCalendar"], "*:0/15")
        backup = unit("todo-backup.timer")["Timer"]
        self.assertEqual(backup["OnCalendar"], "*-*-* 02:30")
        self.assertEqual(backup["Persistent"], "true")

    def test_systemd_accepts_the_units(self):
        analyze = subprocess.run(["sh", "-c", "command -v systemd-analyze"], capture_output=True, text=True)
        if analyze.returncode:
            self.skipTest("systemd-analyze is not installed")
        for name in ("todo-dr-check.timer", "todo-backup.timer"):
            result = subprocess.run(["systemd-analyze", "calendar",
                                     unit(name)["Timer"]["OnCalendar"]], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class SystemdRunner:
    """Runs the file commands for real in a temporary home; answers systemctl from its own state."""

    def __init__(self):
        self.enabled = set()
        self.calls = []

    def __call__(self, argv, input=None, capture_output=True, text=True, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] != "systemctl":
            return subprocess.run(argv, input=input, capture_output=True, text=True, timeout=timeout)
        action, *names = argv[2:]
        if action in ("is-enabled", "is-active"):
            on = names[0] in self.enabled
            answer = ("enabled" if on else "disabled") if action == "is-enabled" else ("active" if on else "inactive")
            return subprocess.CompletedProcess(argv, 0 if on else (1 if action == "is-enabled" else 3), answer, "")
        if action == "enable":
            self.enabled.add(names[-1])
        return subprocess.CompletedProcess(argv, 0, "", "")


class InstallTimerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.runner = SystemdRunner()
        spec = inventory.HostSpec(name="todo-standby", role="standby", address="192.0.2.11", user="ops",
                                  home=str(self.home), local=True)
        self.host = Host(spec, runner=self.runner)

    def install(self):
        self.runner.calls.clear()
        return steps.install_timer(ROOT, self.host, "todo-dr-check")

    def systemctl(self):
        return [call[2:] for call in self.runner.calls if call[0] == "systemctl"]

    def test_first_install_writes_the_units_reloads_and_turns_the_timer_on(self):
        self.assertTrue(self.install())
        directory = self.home / ".config/systemd/user"
        for name in ("todo-dr-check.service", "todo-dr-check.timer"):
            self.assertEqual((directory / name).read_bytes(), (UNITS / name).read_bytes())
            self.assertEqual(oct((directory / name).stat().st_mode & 0o777), "0o644")
        self.assertIn(["daemon-reload"], self.systemctl())
        self.assertIn(["enable", "--now", "todo-dr-check.timer"], self.systemctl())

    def test_a_repeat_changes_nothing_and_a_changed_unit_is_replaced(self):
        self.install()
        self.assertFalse(self.install())
        self.assertEqual([call for call in self.systemctl() if call[0] not in ("is-enabled", "is-active")], [])
        service = self.home / ".config/systemd/user/todo-dr-check.service"
        service.write_text("[Service]\nExecStart=/bin/false\n")
        self.assertTrue(self.install())
        self.assertEqual(service.read_bytes(), (UNITS / "todo-dr-check.service").read_bytes())
        self.assertIn(["daemon-reload"], self.systemctl())
        self.assertEqual(list(service.parent.glob("*.service.*")), [])  # no temporary file left

    def test_a_timer_someone_turned_off_is_turned_on_again(self):
        self.install()
        self.runner.enabled.clear()
        self.assertTrue(self.install())
        self.assertIn(["enable", "--now", "todo-dr-check.timer"], self.systemctl())


class WhereTheTimersGoTests(unittest.TestCase):
    """install-dr-tool puts the check on both hosts, configure-backup the backup on the primary, rebuild the check."""

    @classmethod
    def setUpClass(cls):
        from tests import test_app_ops_commands as commands
        commands.setUpModule()
        cls.addClassCleanup(commands.tearDownModule)
        cls.commands = commands

    def enabled(self, world):
        return sorted((host, command[-1]) for host, command in world.commands
                      if command[:4] == ["systemctl", "--user", "enable", "--now"])

    def hosts(self, world, current_role="primary", other_role="standby"):
        spec = self.commands.spec
        return (Host(self.commands.cli.LOCAL, runner=world),
                Host(spec("todo-primary", current_role, "192.0.2.10"), runner=world),
                Host(spec("todo-standby", other_role, "192.0.2.11"), runner=world))

    def test_install_dr_tool_turns_the_check_on_on_both_hosts(self):
        world = self.commands.World()
        controller, primary, other = self.hosts(world)
        standby.install_dr_tools(str(self.commands.PROJECT), controller, primary, other)
        self.assertEqual(self.enabled(world), [("todo-primary", "todo-dr-check.timer"),
                                               ("todo-standby", "todo-dr-check.timer")])
        # Each host's settings name its offline bundle, for the readiness part of the check.
        configures = [command for _host, command in world.commands if "configure" in command]
        self.assertEqual(len(configures), 2)
        self.assertEqual(sorted(command[command.index("--bundle") + 1] for command in configures),
                         ["/home/todo-primary/todo-offline-m12", "/home/todo-standby/todo-offline-m12"])
        for command in configures:
            self.assertIn("--revision", command)

    def test_the_revision_comes_from_the_operations_package(self):
        import tempfile
        from pathlib import Path

        from app_ops import steps
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(steps.package_revision(directory), "")
            (Path(directory) / "VERSION").write_text("package=todo-operations\nsource_revision=" + "a" * 40
                                                     + "\nsource_state=clean\n")
            self.assertEqual(steps.package_revision(directory), "a" * 40)

    def test_configure_backup_turns_the_nightly_backup_on_on_the_current_primary(self):
        world = self.commands.World()
        controller, current, _other = self.hosts(world, "current_primary", "rebuild_standby")
        recovery.configure_backup(str(self.commands.PROJECT), controller, current)
        self.assertEqual(self.enabled(world), [("todo-primary", "todo-backup.timer")])

    def test_rebuild_turns_the_check_on_on_the_rebuilt_standby(self):
        world = self.commands.World()
        recovery.rebuild(str(self.commands.PROJECT), *self.commands.RecoveryTests.hosts(None, world),
                         "todo-primary is fenced", "todo-primary")
        self.assertIn(("todo-primary", "todo-dr-check.timer"), self.enabled(world))


if __name__ == "__main__":
    unittest.main()
