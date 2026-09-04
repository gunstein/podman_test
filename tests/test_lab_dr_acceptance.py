import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "lab_dr_acceptance.py"
SPEC = importlib.util.spec_from_file_location("lab_dr_acceptance", SCRIPT)
assert SPEC and SPEC.loader
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class RecordingRunner:
    def __init__(self, results=()):
        self.commands = []
        self.results = iter(results)

    def __call__(self, arguments, input_text=None, timeout=None):
        self.commands.append((list(arguments), input_text, timeout))
        return next(self.results, completed())


def config_text(primary_vmid=107, standby_vmid=108):
    return f"""version = 1
[proxmox]
ssh_target = "labdr@proxmox"
command_timeout_seconds = 60
wait_timeout_seconds = 5
poll_interval_seconds = 0.01
[guest_ssh]
user = "gunstein"
[nodes.primary]
vmid = {primary_vmid}
proxmox_name = "todo-primary-clean"
hostname = "todo-primary"
address = "192.168.0.102"
clean_snapshot = "primary-clean"
[nodes.standby]
vmid = {standby_vmid}
proxmox_name = "todo-standby-clean"
hostname = "todo-standby"
address = "192.168.0.108"
clean_snapshot = "standby-clean"
"""


class LabDrAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "lab.toml"
        self.config_path.write_text(config_text(), encoding="utf-8")
        self.config = lab.load_config(self.config_path)
        self.state = lab.StateStore(self.root / "state.json")

    def tearDown(self):
        self.temporary.cleanup()

    def test_loads_valid_config_and_derives_exact_confirmation(self):
        self.assertEqual(self.config.reset_confirmation, "107:108")
        self.assertEqual(
            [node.hostname for node in self.config.nodes],
            ["todo-primary", "todo-standby"],
        )

    def test_rejects_duplicate_vmids(self):
        self.config_path.write_text(
            config_text(primary_vmid=107, standby_vmid=107),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(lab.LabError, "VMIDs must differ"):
            lab.load_config(self.config_path)

    def test_state_file_is_private_and_bound_to_config(self):
        state = self.state.load()
        state["config_fingerprint"] = self.config.fingerprint
        state["stages"]["reset_hosts"] = {"status": "completed"}
        self.state.save(state)
        self.assertEqual(os.stat(self.state.path).st_mode & 0o777, 0o600)

        changed = self.config_path.read_text().replace(
            "standby-clean", "different-snapshot"
        )
        self.config_path.write_text(changed, encoding="utf-8")
        other = lab.load_config(self.config_path)
        controller = lab.LabAcceptance(other, self.state)
        with self.assertRaisesRegex(lab.LabError, "different lab configuration"):
            controller.report("reset-check")

    def test_proxmox_validation_checks_exact_name_and_snapshot(self):
        runner = RecordingRunner(
            [
                completed(stdout="name: todo-primary-clean\n"),
                completed(stdout="`-> primary-clean 2026-09-03\n"),
            ]
        )
        adapter = lab.ProxmoxAdapter(self.config, runner)
        adapter.validate_node(self.config.primary)
        commands = [entry[0] for entry in runner.commands]
        self.assertEqual(commands[0][-3:], ["qm", "config", "107"])
        self.assertEqual(commands[1][-3:], ["qm", "listsnapshot", "107"])

        ping_runner = RecordingRunner([completed()])
        adapter = lab.ProxmoxAdapter(self.config, ping_runner)
        adapter.wait_guest_agent(self.config.primary)
        self.assertEqual(
            ping_runner.commands[0][0][-5:],
            ["qm", "guest", "cmd", "107", "ping"],
        )

    def test_wrong_reset_confirmation_changes_nothing(self):
        proxmox = mock.Mock()
        controller = lab.LabAcceptance(
            self.config,
            self.state,
            proxmox=proxmox,
            guests=mock.Mock(),
        )
        with self.assertRaisesRegex(lab.LabError, "confirm-reset"):
            controller.reset_hosts("reset-check", "wrong")
        proxmox.assert_not_called()
        self.assertFalse(self.state.path.exists())

    def test_validation_failure_does_not_start_destructive_reset(self):
        proxmox = mock.Mock()
        proxmox.validate_node.side_effect = lab.LabError("unavailable")
        controller = lab.LabAcceptance(
            self.config, self.state, proxmox=proxmox, guests=mock.Mock()
        )

        with self.assertRaises(lab.LabError):
            controller.reset_hosts("reset-check", "107:108")

        self.assertFalse(self.state.path.exists())

    def test_completed_reset_resumes_without_confirmation(self):
        state = self.state.load()
        state.update(
            {
                "config_fingerprint": self.config.fingerprint,
                "profile": "reset-check",
            }
        )
        state["stages"]["reset_hosts"] = {"status": "completed"}
        self.state.save(state)
        proxmox = mock.Mock()
        controller = lab.LabAcceptance(
            self.config, self.state, proxmox=proxmox, guests=mock.Mock()
        )

        controller.reset_hosts("reset-check", "")

        proxmox.assert_not_called()

    def test_destructive_reset_is_not_retried_after_failure(self):
        proxmox = mock.Mock()
        proxmox.stop.side_effect = lab.LabError("stop failed")
        controller = lab.LabAcceptance(
            self.config,
            self.state,
            proxmox=proxmox,
            guests=mock.Mock(),
        )
        with self.assertRaises(lab.LabError):
            controller.reset_hosts("reset-check", "107:108")
        with self.assertRaisesRegex(lab.LabError, "already started"):
            controller.reset_hosts("reset-check", "107:108")

    def test_guest_preflight_returns_machine_id_and_checks_address(self):
        machine_id = "a" * 32
        runner = RecordingRunner(
            [completed(stdout=f"active\nLAB_MACHINE_ID={machine_id}\n")]
        )
        adapter = lab.GuestAdapter(self.config, runner)

        self.assertEqual(
            adapter.clean_preflight(self.config.primary), machine_id
        )
        script = runner.commands[0][1]
        self.assertIn(" 192.168.0.102/", script)
        self.assertIn("getenforce", script)
        self.assertIn("podman ps -a --format", script)
        self.assertIn("podman volume ls --format", script)
        self.assertIn("podman secret ls --format", script)
        self.assertIn("podman network ls --format", script)
        self.assertIn("todo*.kube", script)
        self.assertNotIn('test -z "$(podman volume ls -q)"', script)

    def test_clean_preflight_rejects_duplicate_machine_ids(self):
        state = self.state.load()
        state.update(
            {
                "config_fingerprint": self.config.fingerprint,
                "profile": "reset-check",
            }
        )
        state["stages"]["reset_hosts"] = {"status": "completed"}
        self.state.save(state)
        guests = mock.Mock()
        guests.clean_preflight.side_effect = ["a" * 32, "a" * 32]
        controller = lab.LabAcceptance(
            self.config, self.state, proxmox=mock.Mock(), guests=guests
        )

        with self.assertRaisesRegex(lab.LabError, "machine IDs must differ"):
            controller.clean_preflight("reset-check")

        self.assertEqual(
            self.state.load()["stages"]["clean_preflight"]["status"],
            "failed",
        )

    def test_clean_preflight_requires_completed_reset(self):
        controller = lab.LabAcceptance(
            self.config,
            self.state,
            proxmox=mock.Mock(),
            guests=mock.Mock(),
        )
        with self.assertRaisesRegex(lab.LabError, "must be completed"):
            controller.clean_preflight("reset-check")

    def test_report_is_machine_readable(self):
        state = self.state.load()
        state.update(
            {
                "config_fingerprint": self.config.fingerprint,
                "profile": "reset-check",
            }
        )
        state["stages"]["reset_hosts"] = {"status": "completed"}
        self.state.save(state)
        controller = lab.LabAcceptance(self.config, self.state)
        with mock.patch("builtins.print") as output:
            controller.report("reset-check", as_json=True)
        report = json.loads(output.call_args.args[0])
        self.assertEqual(report["overall"], "INCOMPLETE")
        self.assertEqual(
            report["stages"]["clean_preflight"]["status"], "pending"
        )


if __name__ == "__main__":
    unittest.main()
