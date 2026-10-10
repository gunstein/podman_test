"""acceptance.py step: one guide line at a time, as written, only after the step before it passed.

These tests run real bash on a small guide, so the helpers and the gate are
tested the way an agent run uses them. Runs 24 and 26 stopped because the
agent changed a command and ran on past a failure; step makes both impossible.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/scripts/lab"))
import acceptance  # noqa: E402

GUIDE = """# A guide

```bash
product 01-1-first echo one   # → one
product 01-2-json echo '{"changed": false}'   # → {"changed": true}
product 01-3-after echo after
```

```bash
product 02-1-refused false   # exit=1, a refusal the guide asks for
$A --step 02-2 check headers
product 02-3-last echo last
```
"""


class StepTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory))
        self.runs = directory / "runs"
        guide = directory / "guide.md"
        guide.write_text(GUIDE)
        # AGENT_GUIDE is joined to ROOT; an absolute path replaces it.
        for patcher in (patch.object(acceptance, "AGENT_GUIDE", str(guide)),
                        patch.dict(os.environ, {"ACCEPTANCE_RUNS": str(self.runs)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.run_directory = self.runs / "r1"
        self.readiness("# start 2026-10-03T10:00:00+02:00\nPASS  ...\n\nREADY for the agent run.\nexit=0\n")

    def readiness(self, text):
        """Write logs/00-readiness.log, as the C1a line does; None removes it."""
        log = self.run_directory / "logs" / "00-readiness.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            log.unlink(missing_ok=True)
        else:
            log.write_text(text)

    def step(self, name):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = acceptance.main(["--run", "r1", "step", name])
        return code, output.getvalue() + errors.getvalue()

    def log(self, name):
        return (self.run_directory / "logs" / f"{name}.log").read_text().splitlines()

    def record(self, step, result):
        self.run_directory.mkdir(parents=True, exist_ok=True)
        acceptance.append_record(self.run_directory, {
            "step": step, "kind": "check", "command": "headers", "arguments": [], "result": result,
            "values": {}, "log": f"logs/{step}-check-headers.log", "approved": ""})

    def test_the_first_step_needs_a_passing_readiness_check_on_record(self):
        """Run 34 ran the readiness check in the terminal only, and was not clean."""
        for text, message in ((None, "has not run into logs/00-readiness.log"),
                              ("# start a\nNOT READY: 2 checks failed.\nexit=1\n", "did not end with READY"),
                              ("# start a\nREADY for the agent run.\nexit=0\n# start b\nFAIL\nexit=1\n",
                               "did not end with READY")):
            with self.subTest(message=message):
                self.readiness(text)
                code, output = self.step("01-1-first")
                self.assertEqual(code, 3)
                self.assertIn(message, output)
                self.assertFalse((self.run_directory / "logs" / "01-1-first.log").exists())
        self.readiness("# start a\nFAIL\nexit=1\n# start b\nREADY for the agent run.\nexit=0\n")
        self.assertEqual(self.step("01-1-first")[0], 0)

    def test_steps_come_from_the_guide_in_its_order(self):
        steps = acceptance.guide_lines(GUIDE)
        self.assertEqual([name for name, _ in steps],
                         ["01-1-first", "01-2-json", "01-3-after", "02-1-refused", "02-2", "02-3-last"])

    def test_a_step_runs_the_guide_line_and_logs_the_exact_command(self):
        code, output = self.step("01-1-first")
        self.assertEqual(code, 0, output)
        self.assertIn("STEP 01-1-first: PASS", output)
        log = self.log("01-1-first")
        self.assertTrue(log[0].startswith("# start "))
        self.assertEqual(log[1:], ["# command: echo one", "one", "exit=0"])

    def test_only_the_next_step_runs(self):
        code, output = self.step("01-3-after")
        self.assertEqual(code, 3)
        self.assertIn("the next step is 01-1-first", output)
        self.assertFalse((self.run_directory / "logs/01-3-after.log").exists())
        code, output = self.step("09-9-unknown")
        self.assertEqual(code, 3)
        self.assertIn("is not a step", output)

    def test_a_step_runs_once(self):
        """A passed step is not run again: the call says so and names the next step (run 43)."""
        self.step("01-1-first")
        code, output = self.step("01-1-first")
        self.assertEqual(code, 0)
        self.assertIn("STEP 01-1-first: already passed, nothing run", output)
        self.assertIn("NEXT: $A step 01-2-json", output)
        self.assertEqual(len(self.log("01-1-first")), 4)

    def test_the_expected_json_must_be_printed_and_nothing_runs_after_a_stop(self):
        self.step("01-1-first")
        code, output = self.step("01-2-json")
        self.assertEqual(code, 1)
        self.assertIn('STOP, did not print {"changed": true}', output)
        code, output = self.step("01-3-after")
        self.assertEqual(code, 3)
        self.assertIn("01-2-json did not print", output)
        self.assertFalse((self.run_directory / "logs/01-3-after.log").exists())

    def test_a_refused_step_must_exit_1_and_a_tool_step_must_pass(self):
        logs = self.run_directory / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        for name, text in (("01-1-first", "one"), ("01-2-json", '{"changed": true}'), ("01-3-after", "after")):
            (logs / f"{name}.log").write_text(f"# start now\n{text}\nexit=0\n")
        self.record("02-2", "FAIL")  # recorded first, so 02-1 does not run it as the check after it
        code, output = self.step("02-1-refused")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.log("02-1-refused")[-1], "exit=1")
        code, output = self.step("02-3-last")
        self.assertEqual(code, 3)
        self.assertIn("02-2 FAIL", output)

    def test_a_failed_check_may_run_once_more_and_then_the_next_step_runs(self):
        logs = self.run_directory / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        for name, text in (("01-1-first", "one"), ("01-2-json", '{"changed": true}'), ("01-3-after", "after"),
                           ("02-1-refused", "no")):
            (logs / f"{name}.log").write_text(f"# start now\n{text}\nexit={int(name.endswith('refused'))}\n")
        self.record("02-2", "FAIL")
        with patch.object(acceptance.subprocess, "run") as run:
            run.side_effect = lambda *a, **k: self.record("02-2", "PASS")
            code, output = self.step("02-2")
        self.assertEqual(code, 0, output)
        self.record("02-2", "FAIL")  # a second failure: no third try
        code, output = self.step("02-2")
        self.assertEqual(code, 3)
        self.assertIn("already ran", output)

    def test_the_checks_right_after_a_step_run_in_the_same_call(self):
        """A2: consecutive read-only checks need no agent turn each; a do, a product line or a block end waits."""
        Path(acceptance.AGENT_GUIDE).write_text(
            "```bash\nproduct 01-1-first echo one\n$A --step 01-2 check headers\n$A --step 01-3 check browser\n"
            "$A --step 01-4 do markers phase1\n$A --step 01-5 check headers\n```\n\n"
            "```bash\n$A --step 02-1 check headers\n```\n")
        real_run, results = acceptance.subprocess.run, {}

        def run(argv, **keywords):
            step = re.search(r"--step (\S+)", argv[-1])
            if not step:
                return real_run(argv, **keywords)
            self.record(step.group(1), results.get(step.group(1), "PASS"))
            return None

        with patch.object(acceptance.subprocess, "run", side_effect=run):
            code, output = self.step("01-1-first")
            self.assertEqual(code, 0, output)
            for name in ("01-1-first", "01-2", "01-3"):
                self.assertIn(f"STEP {name}: PASS", output)
            self.assertNotIn("01-4", output.split("NEXT:")[0])
            self.assertIn("NEXT: $A step 01-4", output)
            results["01-5"] = "FAIL"
            code, output = self.step("01-4")
            self.assertEqual(code, 1, output)
            self.assertIn("STEP 01-5: STOP, FAIL", output)
            self.assertNotIn("NEXT:", output)
            self.assertNotIn("02-1", output)

    def test_a_step_that_is_still_running_holds_the_next_one(self):
        logs = self.run_directory / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "01-1-first.log").write_text("# start now\n# command: echo one\n")
        code, output = self.step("01-2-json")
        self.assertEqual(code, 3)
        self.assertIn("01-1-first is still running", output)

    def test_a_line_ending_in_an_ampersand_starts_in_the_background(self):
        guide = Path(acceptance.AGENT_GUIDE)
        guide.write_text("```bash\nproduct 01-1-long echo done &   # wait for exit=\n```\n")
        code, output = self.step("01-1-long")
        self.assertEqual(code, 0, output)
        self.assertIn("STARTED in the background", output)
        log = self.run_directory / "logs/01-1-long.log"
        for _ in range(100):
            if log.exists() and log.read_text().endswith("exit=0\n"):
                break
            __import__("time").sleep(0.05)
        self.assertEqual(log.read_text().splitlines()[1:], ["# command: echo done", "done", "exit=0"])


class RunAllTests(unittest.TestCase):
    """acceptance.py run: the whole guide in order, without an agent, stopping at the first failure."""

    GUIDE = """```bash
python3 deploy/scripts/lab/acceptance_preflight.py --only-prints-in-part-a
```

```bash
{ echo "# start now"; echo acceptance_preflight.py; echo "READY for the agent run."; echo "exit=0"; } >> "$RUN/logs/00-readiness.log"
```

```bash
product 01-1-first echo one   # → one
product 01-2-long sh -c 'sleep 0.2; echo long' &
product 01-3-after echo after   # → after
```

```bash
IP=192.168.0.102
echo "trust $IP"
```
"""

    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory))
        self.runs, self.guide = directory / "runs", directory / "guide.md"
        self.guide.write_text(self.GUIDE)
        (directory / "xdg").mkdir()
        for patcher in (patch.object(acceptance, "AGENT_GUIDE", str(self.guide)),
                        patch.dict(os.environ, {"ACCEPTANCE_RUNS": str(self.runs), "XDG_RUNTIME_DIR": str(directory / "xdg")}),
                        patch.object(acceptance, "checkout", return_value=("abc123", True)),
                        patch.object(acceptance, "report", return_value=("# Report\n\n**ALL STEPS PASS**\n\n## Steps\n", True)),
                        patch.object(acceptance, "POLL_SECONDS", 0.05),
                        patch.object(acceptance, "CLIENT_TRUST", {"01-3-after": "192.168.0.102"}),
                        patch.object(acceptance, "client_trust", return_value=True),
                        patch.object(acceptance, "sudo_kept", side_effect=contextlib.nullcontext)):
            self.mocks = getattr(self, "mocks", []) + [patcher.start()]
            self.addCleanup(patcher.stop)
        self.password = directory / "xdg/todo-acceptance/e2e-password"

    def run_all(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = acceptance.main(["--run", "r1", "run"])
        return code, output.getvalue()

    def test_every_step_runs_in_order_with_readiness_trust_and_a_background_wait(self):
        code, output = self.run_all()
        self.assertEqual(code, 0, output)
        logs = self.runs / "r1/logs"
        self.assertIn("READY for the agent run.", (logs / "00-readiness.log").read_text())
        for name in ("01-1-first", "01-2-long", "01-3-after"):
            self.assertTrue((logs / f"{name}.log").read_text().endswith("exit=0\n"), name)
        self.assertIn("STEP 01-3-after: PASS", output)
        acceptance.client_trust.assert_called_once()
        self.assertEqual(acceptance.client_trust.call_args.args[2], "192.168.0.102")
        acceptance.sudo_kept.assert_called_once()
        self.assertFalse(self.password.exists(), "the password file is removed at the end")
        self.assertTrue((self.runs / "r1/EVIDENCE.md").exists())
        self.assertIn("ALL STEPS PASS", output)

    def test_a_failed_step_stops_the_run_and_a_second_run_does_not_repeat_it(self):
        self.guide.write_text(self.GUIDE.replace("echo one   # → one", "echo two   # → one"))
        code, output = self.run_all()
        self.assertEqual(code, 1)
        self.assertIn("STEP 01-1-first: STOP, did not print one", output)
        self.assertIn("two", output, "a stopped step shows its output")
        self.assertFalse((self.runs / "r1/logs/01-2-long.log").exists())
        self.assertTrue(self.password.exists(), "kept for a run that goes on after a fix")
        code, output = self.run_all()
        self.assertEqual(code, 1)
        self.assertIn("STOP: 01-1-first did not print one", output)

    def test_a_dirty_checkout_a_refused_password_or_a_failed_readiness_check_runs_nothing(self):
        acceptance.checkout.return_value = ("abc123", False)
        self.assertEqual(self.run_all()[0], 1)
        acceptance.sudo_kept.assert_not_called()
        acceptance.checkout.return_value = ("abc123", True)
        acceptance.sudo_kept.side_effect = acceptance.Refused("sudo did not accept the password")
        code, output = self.run_all()
        self.assertEqual(code, 1)
        self.assertIn("STOP: sudo did not accept the password", output)
        self.assertFalse((self.runs / "r1/logs/00-readiness.log").exists())
        acceptance.sudo_kept.side_effect = contextlib.nullcontext
        acceptance.checkout.return_value = ("abc123", True)
        self.guide.write_text(self.GUIDE.replace('echo "READY for the agent run."; echo "exit=0"', 'echo "exit=1"'))
        code, output = self.run_all()
        self.assertEqual(code, 1)
        self.assertIn("STOP: the readiness check", output)
        self.assertFalse((self.runs / "r1/logs/01-1-first.log").exists())


class SudoKeptTests(unittest.TestCase):
    """One sudo prompt at the start; renewed without a prompt; dropped at the end, even after Ctrl-C."""

    def kept(self, accepted=True, interrupt=False):
        commands = []

        def run(argv, **keywords):
            commands.append(argv)
            return subprocess.CompletedProcess(argv, 0 if accepted or argv != ["sudo", "-v"] else 1)

        with patch.object(acceptance.subprocess, "run", side_effect=run), \
                patch.object(acceptance, "SUDO_REFRESH_SECONDS", 0.01), contextlib.redirect_stdout(io.StringIO()):
            try:
                with acceptance.sudo_kept():
                    time.sleep(0.1)
                    if interrupt:
                        raise KeyboardInterrupt
            except KeyboardInterrupt:
                pass
        return commands

    def test_it_asks_once_renews_without_a_prompt_and_drops_the_timestamp(self):
        for interrupt in (False, True):
            commands = self.kept(interrupt=interrupt)
            self.assertEqual(commands[0], ["sudo", "-v"])
            self.assertEqual(commands[-1], ["sudo", "-k"])
            renewals = commands[1:-1]
            self.assertTrue(renewals)
            self.assertEqual({tuple(argv) for argv in renewals}, {("sudo", "-n", "-v")})

    def test_a_refused_password_stops_before_anything_runs(self):
        with self.assertRaisesRegex(acceptance.Refused, "did not accept"):
            self.kept(accepted=False)


class ClientTrustTests(unittest.TestCase):
    """The guide's C9.4 lines for one address, with the person's sudo, then checked."""

    def trust(self, mapped, answers=0):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory))
        hosts = directory / "hosts"
        hosts.write_text(f"{mapped} {' '.join(acceptance.HOSTNAMES)}\n")
        commands = []

        def run(argv, **keywords):
            commands.append(argv)
            output = "** WARNING: connection is not using a post-quantum key exchange algorithm.\ntrusted\n"
            return subprocess.CompletedProcess(argv, 0 if argv[0] == "bash" else answers, output, "")

        guide = (ROOT / acceptance.AGENT_GUIDE).read_text()
        shown = io.StringIO()
        with patch.object(acceptance, "HOSTS", hosts), patch.object(acceptance.subprocess, "run", side_effect=run), \
                contextlib.redirect_stdout(shown):
            passed = acceptance.client_trust(directory, guide, "192.168.0.108")
        self.shown = shown.getvalue()
        return passed, commands, (directory / "operator/client-trust-192.168.0.108.log").read_text()

    def test_the_guide_lines_run_for_the_address_and_both_names_must_answer(self):
        passed, commands, record = self.trust("192.168.0.108")
        self.assertTrue(passed)
        script = commands[0][2]
        self.assertIn("IP=192.168.0.108", script)
        # Every sudo, also in trust-serving-ca.sh, uses the cached timestamp and never prompts.
        self.assertTrue(script.startswith("set -e\n" + acceptance.SUDO_NO_PROMPT))
        self.assertNotIn("password", self.shown)
        self.assertIn('trust-serving-ca.sh "gunstein@$IP"', script)
        self.assertEqual([argv[-1] for argv in commands[1:]], ["https://todo.test:8443/ready",
                                                              "https://notes.test:8443/ready"])
        self.assertTrue(record.endswith("exit=0\n"))
        self.assertIn("post-quantum", record, "the log keeps everything")
        self.assertNotIn("post-quantum", self.shown, "the terminal does not show SSH's warning")

    def test_a_wrong_hosts_line_or_no_https_answer_fails(self):
        self.assertFalse(self.trust("192.168.0.102")[0])
        self.assertFalse(self.trust("192.168.0.108", answers=22)[0])

    def test_the_real_guide_has_what_run_needs(self):
        guide = (ROOT / acceptance.AGENT_GUIDE).read_text()
        names = [name for name, _ in acceptance.guide_lines(guide)]
        for name in acceptance.CLIENT_TRUST:
            self.assertIn(name, names)
        # Run 46: the first block with the preflight was Part A's, which writes no log.
        block = acceptance.guide_block(guide, "00-readiness.log")
        self.assertTrue([line for line in block.splitlines()
                         if "acceptance_preflight.py" in line and '>> "$RUN/logs/00-readiness.log"' in line])
        self.assertRegex(acceptance.guide_block(guide, "trust-serving-ca.sh"), r"(?m)^IP=\S+$")


class ExpectationTests(unittest.TestCase):
    """What a product step must print, read from the comment after '→'."""

    def state(self, comment, *output, name="05-1-x"):
        with tempfile.TemporaryDirectory() as run:
            logs = Path(run) / "logs"
            logs.mkdir()
            (logs / f"{name}.log").write_text("\n".join(
                ["# start 2026-10-06T12:00:00+02:00", "# command: true",
                 "** WARNING: connection is not using a post-quantum key exchange algorithm.", *output, "exit=0"]) + "\n")
            return acceptance.product_state(Path(run), name, f"vm {name} 192.168.0.102 'true'   # → {comment}")

    def test_each_form(self):
        cases = [('{"changed": false}', ['{"changed": false, "x": 1}'], ['{"changed": true}']),
                 ("nothing", [], ["something"]),
                 ('3× "Writable: no"', ["Writable: no"] * 3, ["Writable: no"] * 2 + ["Writable: yes"]),
                 ('only "0"', ["0", "", "0", "", "0"], ["0", "10", "0"]),
                 ('"rich rule"', ["app-ops: zone public has no rich rule in it"], ["app-ops: other"]),
                 ("1", ["        1", "(1 row)"], ["        0"]),
                 ('"a: 0", "b: 3"', ["a: 0", "b: 3"], ["a: 0", "b: 2"])]
        for comment, good, bad in cases:
            with self.subTest(comment=comment):
                self.assertEqual(self.state(comment, *good), "passed")
                self.assertTrue(self.state(comment, *bad).startswith("did not print"), comment)

    def test_only_needs_one_line_and_ssh_warnings_and_headers_do_not_count(self):
        self.assertEqual(self.state('only "0"'), 'did not print only "0"')
        self.assertEqual(self.state("nothing"), "passed")

    def test_an_unreadable_expectation_is_an_error(self):
        with self.assertRaises(ValueError):
            acceptance.expectations('vm 05-1-x h \'true\'   # → 3× unquoted')


class HelperTests(unittest.TestCase):
    """deploy/scripts/lab/helpers.sh, which every guide line runs with."""

    def bash(self, run, script):
        import subprocess
        return subprocess.run(["bash", "-c", f"source deploy/scripts/lab/helpers.sh\n{script}"], cwd=ROOT,
                              env={"RUN": str(run), "PATH": os.environ["PATH"]}, capture_output=True, text=True)

    def test_backup_names_and_the_recorded_onboot_are_read_from_the_run(self):
        with tempfile.TemporaryDirectory() as run:
            (Path(run) / "logs").mkdir()
            (Path(run) / "logs/08-4-backup-create.log").write_text(
                "# start 2026-09-28T19:19:40+02:00\n"
                "todo: Verified base backup: base-20260928T191946Z\n"
                "notes: Verified base backup: base-20260928T191948Z\n"
                "keycloak: Verified base backup: base-20260928T191950Z\n"
                "exit=0\n")
            (Path(run) / "record.jsonl").write_text(
                json.dumps({"step": "01-3", "result": "STARTED", "values": {}}) + "\n"
                + json.dumps({"step": "01-3", "result": "PASS", "values": {"onboot": 1}}) + "\n")
            (Path(run) / "logs/08-7-mark.log").write_text(
                "# start 2026-10-04T14:36:00+02:00\n"
                "todo: Archived restore point acceptance_before_after at 0/5000100\n"
                "2026-10-04T12:36:05Z\n"
                "exit=0\n")
            result = self.bash(run, 'echo "$(backup_name todo) $(backup_name notes) $(recorded_onboot) $(restore_time)"')
        self.assertEqual(result.stdout, "base-20260928T191946Z base-20260928T191948Z 1 2026-10-04T12:36:05Z\n",
                         result.stderr)

    def test_a_second_run_of_a_product_step_refuses_and_keeps_the_first_log(self):
        with tempfile.TemporaryDirectory() as run:
            (Path(run) / "logs").mkdir()
            result = self.bash(run, "product 06-10-x echo first\nproduct 06-10-x echo second")
            log = (Path(run) / "logs/06-10-x.log").read_text().splitlines()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("STOP:", result.stderr)
        self.assertEqual(log[1:], ["# command: echo first", "first", "exit=0"])


def natural(name):
    """A step name as numbers and words, so 05-8-3f comes before 05-8-4a and 08-9 before 08-10."""
    import re
    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.findall(r"[0-9]+|[a-z]+", name)]


class AgentGuideStepTests(unittest.TestCase):
    """The real guide: every step runs in the order its names give, each name once."""

    def test_every_comment_check_is_an_expectation_the_tool_reads(self):
        """Every product line's check is machine-read: an expectation after '→', or only its exit code.

        Run 33 stopped at 03-12c because its comment began with a description
        the command never prints. Since then a comment that says what output
        must look like is written as an expectation (EXPECTATION), so neither
        an agent nor a person has to judge it.
        """
        text = (ROOT / acceptance.AGENT_GUIDE).read_text()
        for name, line in acceptance.guide_lines(text):
            if line.startswith('$A ') or '#' not in line:
                continue
            with self.subTest(step=name):
                found = acceptance.expectations(line)
                self.assertTrue(found or name == '02-1-build-offline', line[-80:])

    def test_the_guide_steps_are_in_order_and_unique(self):
        steps = acceptance.guide_lines((ROOT / acceptance.AGENT_GUIDE).read_text())
        names = [name for name, _ in steps]
        self.assertGreater(len(names), 150)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names, sorted(names, key=natural))
        for name in ("03-4a-browser-env", "03-4b-provision-user", "04-4-sudo-refusal-skip",
                     "06-6-preflight", "06-10-failover", "09-12b"):
            self.assertIn(name, names)

    def test_long_steps_run_in_the_background(self):
        steps = dict(acceptance.guide_lines((ROOT / acceptance.AGENT_GUIDE).read_text()))
        for name in ("02-1-build-offline", "04-10-bootstrap", "06-10-failover", "09-10-rebuild"):
            self.assertTrue(acceptance.without_comment(steps[name]).endswith("&"), name)

    def test_confirmations_are_in_the_lines_step_runs(self):
        steps = dict(acceptance.guide_lines((ROOT / acceptance.AGENT_GUIDE).read_text()))
        self.assertIn("--confirm-primary-fenced 'todo-primary is fenced'", steps["06-6-preflight"])
        self.assertIn("--confirm-primary-fenced 'todo-primary is fenced' --confirm-promotion todo-standby",
                      steps["06-10-failover"])


if __name__ == "__main__":
    unittest.main()
