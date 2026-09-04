import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "todo_dr_run.py"
SPEC = importlib.util.spec_from_file_location("todo_dr_run", SCRIPT)
assert SPEC and SPEC.loader
todo_dr_run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(todo_dr_run)


def completed(returncode=0):
    return subprocess.CompletedProcess([], returncode)


class RecordingRunner:
    def __init__(self, returncodes=()):
        self.commands = []
        self.returncodes = iter(returncodes)

    def __call__(self, arguments):
        self.commands.append(list(arguments))
        return completed(next(self.returncodes, 0))


class TodoDrRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "ansible").mkdir()
        for playbook in (
            "deploy-promoted-application.yml",
            "preflight-standby-rebuild.yml",
            "rebuild-standby.yml",
            "cluster-status.yml",
        ):
            (self.root / "ansible" / playbook).touch()
        self.inventory = self.root / "inventory.ini"
        self.inventory.write_text("[todo_current_primary]\nnode\n", encoding="utf-8")
        self.state = todo_dr_run.StateStore(self.root / "state.json")
        self.runner = RecordingRunner()
        self.run = todo_dr_run.DrRun(
            self.root,
            self.inventory,
            self.state,
            dr_tool=self.root / "todo_dr.py",
            runner=self.runner,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def complete(self, *stages):
        state = self.state.load()
        state["inventory"] = str(self.inventory.resolve())
        for stage in stages:
            state["stages"][stage] = {"status": "completed"}
        self.state.save(state)

    def test_installed_runner_defaults_to_extracted_operations_package(self):
        installed = Path("/opt/todo/bin/todo_dr_run.py")
        self.assertEqual(
            todo_dr_run.default_project_root(installed),
            Path.home() / "todo-operations",
        )

    def test_missing_playbook_fails_with_project_root_guidance(self):
        self.complete("promotion", "application", "rebuild")
        (self.root / "ansible" / "cluster-status.yml").unlink()
        with self.assertRaisesRegex(todo_dr_run.RunError, "--project-root"):
            self.run.verify()

    def test_state_file_is_private(self):
        self.complete("promotion")
        self.assertEqual(os.stat(self.state.path).st_mode & 0o777, 0o600)

    def test_promotion_runs_preflight_before_promote(self):
        self.run.promote("todo-primary is fenced", "todo-standby")
        self.assertEqual(self.runner.commands[0][2], "preflight")
        self.assertEqual(self.runner.commands[1][2], "promote")
        self.assertEqual(
            self.state.load()["stages"]["promotion"]["status"], "completed"
        )

    def test_failed_preflight_does_not_start_promotion(self):
        self.runner.returncodes = iter([1])
        with self.assertRaises(todo_dr_run.RunError):
            self.run.promote("wrong", "todo-standby")
        self.assertNotIn("promotion", self.state.load()["stages"])

    def test_rebuild_preflight_precedes_destructive_stage(self):
        self.complete("promotion", "application")
        self.run.rebuild("todo-primary is fenced", "todo-primary")
        names = [Path(command[3]).name for command in self.runner.commands]
        self.assertEqual(
            names,
            ["preflight-standby-rebuild.yml", "rebuild-standby.yml"],
        )

    def test_failed_destructive_stage_cannot_be_retried(self):
        self.complete("promotion", "application")
        self.runner.returncodes = iter([0, 1])
        with self.assertRaises(todo_dr_run.RunError):
            self.run.rebuild("todo-primary is fenced", "todo-primary")
        self.runner.returncodes = iter([0])
        with self.assertRaisesRegex(todo_dr_run.RunError, "will not retry"):
            self.run.rebuild("todo-primary is fenced", "todo-primary")

    def test_verify_runs_after_rebuild(self):
        self.complete("promotion", "application", "rebuild")
        self.run.verify()
        names = [Path(command[3]).name for command in self.runner.commands]
        self.assertEqual(names, ["cluster-status.yml"])

    def test_verify_requires_rebuild(self):
        self.complete("promotion", "application")
        with self.assertRaisesRegex(todo_dr_run.RunError, "rebuild"):
            self.run.verify()

    def test_become_prompt_is_forwarded_only_when_requested(self):
        privileged = todo_dr_run.DrRun(
            self.root,
            self.inventory,
            self.state,
            dr_tool=self.root / "todo_dr.py",
            runner=self.runner,
            ask_become_pass=True,
        )
        self.complete("promotion")
        privileged.deploy_application()
        self.assertIn("--ask-become-pass", self.runner.commands[0])

    def test_inventory_change_is_rejected(self):
        state = self.state.load()
        state["inventory"] = "/different/inventory.ini"
        self.state.save(state)
        with self.assertRaisesRegex(todo_dr_run.RunError, "State belongs"):
            self.run.show_status()


if __name__ == "__main__":
    unittest.main()
