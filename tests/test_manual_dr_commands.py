import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ManualCommandsTests(unittest.TestCase):
    def run_phase(self, phase, text=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = directory / "lab.toml"
            config.write_text(text or (ROOT / "lab-dr.example.toml").read_text())
            # No external executable can be found: generator must only print.
            return subprocess.run(
                [sys.executable, str(ROOT / "scripts/manual_dr_commands.py"),
                 "--config", str(config), phase],
                env={**os.environ, "PATH": str(directory)},
                capture_output=True, text=True, timeout=10,
            )

    def test_every_phase_is_print_only_and_uses_guest_not_hypervisor(self):
        for phase in ("status", "prepare-quarantine", "promote", "application",
                      "application-repeat", "backup", "rebuild-preflight",
                      "rebuild", "cluster-status"):
            with self.subTest(phase=phase):
                result = self.run_phase(phase)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("PRINT ONLY", result.stdout)
                self.assertIn("StrictHostKeyChecking=yes", result.stdout)
                self.assertNotIn("labdr@proxmox", result.stdout)
                address = "192.168.0.102" if phase == "prepare-quarantine" else "192.168.0.108"
                self.assertIn("gunstein@" + address, result.stdout)

    def test_addresses_are_read_from_config(self):
        config = (ROOT / "lab-dr.example.toml").read_text().replace(
            "192.168.0.108", "192.0.2.88")
        result = self.run_phase("status", config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gunstein@192.0.2.88", result.stdout)
        self.assertNotIn("192.168.0.108", result.stdout)

    def test_destructive_warnings_and_exact_confirmation(self):
        result = self.run_phase("rebuild")
        self.assertIn("DATA DELETION", result.stdout)
        self.assertIn("--confirm-reseed todo-primary", result.stdout)
        self.assertIn("--confirm-old-primary-fenced", result.stdout)
        self.assertIn("todo-primary is fenced", result.stdout)

    def test_privileged_phases_always_prompt_for_become_password(self):
        for phase in ("prepare-quarantine", "application", "application-repeat",
                      "backup", "rebuild-preflight", "rebuild"):
            with self.subTest(phase=phase):
                result = self.run_phase(phase)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ssh -t", result.stdout)
                self.assertIn("--ask-become-pass", result.stdout)

    def test_invalid_topology_rejected(self):
        config = (ROOT / "lab-dr.example.toml").read_text().replace("vmid = 108", "vmid = 107")
        result = self.run_phase("status", config)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ssh -t", result.stdout)
