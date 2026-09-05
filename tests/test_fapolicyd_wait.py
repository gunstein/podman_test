import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WAIT = ROOT / "ansible/roles/todo_fapolicyd/tasks/wait-trust.yml"


class FapolicydWaitTests(unittest.TestCase):
    def test_both_reload_boundaries_wait_before_protected_reads(self):
        tasks = yaml.safe_load((WAIT.parent / "main.yml").read_text())
        names = [task["name"] for task in tasks]
        self.assertLess(names.index("Reload controller fapolicyd trust before reading source files"),
                        names.index("Wait for exact controller source trust before reading files"))
        self.assertLess(names.index("Wait for exact controller source trust before reading files"),
                        names.index("Install exact Todo operator files through trusted stdin"))
        self.assertLess(names.index("Reload target fapolicyd trust"),
                        names.index("Wait for exact installed Todo file trust"))

    def run_wait(self, refresh):
        ansible = shutil.which("ansible-playbook")
        if not ansible:
            candidate = ROOT / "ansible/.venv/bin/ansible-playbook"
            if not candidate.is_file():
                self.skipTest("Ansible is required for the trust-wait integration test")
            ansible = str(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            source = directory / "operator.py"
            source.write_text("print('test')\n")
            contents = source.read_bytes()
            expected = f"filedb {source} {len(contents)} {hashlib.sha256(contents).hexdigest()}"
            current = directory / "current"
            current.write_text(expected + "\n")
            stale = directory / "stale"
            # Existing path with stale hash, plus a prefix collision: neither is trusted.
            stale.write_text(f"filedb {source} {len(contents)} {'0' * 64}\n"
                             + expected.replace(str(source), str(source) + ".bak") + "\n")
            counter = directory / "counter"
            binary = directory / "fapolicyd-cli"
            binary.write_text(
                '#!/bin/sh\nset -eu\n'
                'test "$1" = --dump-db\n'
                'count=0\nif test -f "$TRUST_COUNTER"; then count=$(cat "$TRUST_COUNTER"); fi\n'
                'count=$((count + 1))\nprintf "%s\\n" "$count" > "$TRUST_COUNTER"\n'
                'if test "$TRUST_REFRESH" = yes && test "$count" -gt 1; then\n'
                '  cat "$TRUST_CURRENT"\nelse\n  cat "$TRUST_STALE"\nfi\n'
            )
            binary.chmod(0o755)
            tasks = yaml.safe_load(WAIT.read_text())
            self.assertEqual(tasks[-1]["retries"], 30)
            self.assertEqual(tasks[-1]["delay"], 1)
            tasks[-1]["retries"] = 2
            tasks[-1]["delay"] = 0
            play = directory / "play.yml"
            play.write_text(yaml.safe_dump([{
                "hosts": "localhost", "connection": "local", "gather_facts": False,
                "vars": {"todo_fapolicyd_wait_paths": [str(source)],
                         "ansible_python_interpreter": sys.executable},
                "tasks": tasks,
            }]))
            result = subprocess.run(
                [ansible, "-i", "localhost,", str(play)], capture_output=True,
                text=True, timeout=45, check=False,
                env={**os.environ, "PATH": f"{directory}:{os.environ['PATH']}",
                     "TRUST_COUNTER": str(counter), "TRUST_CURRENT": str(current),
                     "TRUST_STALE": str(stale), "TRUST_REFRESH": "yes" if refresh else "no"},
            )
            return result, int(counter.read_text())

    def test_delayed_database_reload_succeeds_without_changes(self):
        result, reads = self.run_wait(refresh=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreaterEqual(reads, 2)
        self.assertIn("changed=0", result.stdout)

    def test_stale_or_prefix_only_entry_fails_after_bounded_retries(self):
        result, reads = self.run_wait(refresh=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertGreaterEqual(reads, 2)
        self.assertLessEqual(reads, 3)
