"""Run the real trust-files.sh with fake fapolicyd-cli, systemctl, chown and stat."""
import base64
import hashlib
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/scripts/trust-files.sh"

FAKE_CLI = """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$FAKE_DIR/calls"
case "$1" in
  --dump-db)
    count=$(cat "$FAKE_DIR/reads" 2>/dev/null || echo 0); count=$((count + 1)); echo "$count" > "$FAKE_DIR/reads"
    if [ "$count" -gt "${FAKE_STALE_READS:-0}" ]; then cat "$FAKE_DIR/current"; else cat "$FAKE_DIR/stale"; fi ;;
  --file) [ "$2" = add ] || grep -qxF "$3" "$FAKE_DIR/known" ;;
esac
"""


class FakeTrustTools:
    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.directory)])
        binaries = self.directory / "bin"
        binaries.mkdir()
        for name, body in {
            "fapolicyd-cli": FAKE_CLI,
            "systemctl": '#!/bin/sh\n[ "$*" = "is-active --quiet fapolicyd" ] && exit "${FAKE_INACTIVE:-0}"\nexit 99\n',
            "chown": "#!/bin/sh\nexit 0\n",
            "stat": '#!/bin/sh\n[ "$1 $2" = "-c %U:%G" ] && echo root:root && exit 0\nexec /usr/bin/stat "$@"\n',
        }.items():
            (binaries / name).write_text(body)
            (binaries / name).chmod(0o755)
        self.source = self.directory / "todo_tool.py"
        self.source.write_text("print('test')\n")
        contents = self.source.read_bytes()
        self.expected = f"filedb {self.source} {len(contents)} {hashlib.sha256(contents).hexdigest()}"
        (self.directory / "current").write_text("filedb /usr/bin/python3 1 abc\n" + self.expected + "\n")
        # An existing path with a stale hash, plus a prefix collision: neither is trust.
        (self.directory / "stale").write_text(
            f"filedb {self.source} {len(contents)} {'0' * 64}\n"
            + self.expected.replace(str(self.source), str(self.source) + ".bak") + "\n")
        (self.directory / "known").write_text("")

    def run_script(self, *arguments, stdin=None, **environment):
        return subprocess.run(
            ["sh", str(SCRIPT), *arguments], input=stdin, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PATH": f"{self.directory / 'bin'}:{os.environ['PATH']}",
                 "FAKE_DIR": str(self.directory), "TRUST_DELAY": "0", **environment})

    def calls(self):
        path = self.directory / "calls"
        return path.read_text().splitlines() if path.exists() else []


class TrustFilesTests(FakeTrustTools, unittest.TestCase):

    def test_new_path_is_added_reloaded_and_waited_for(self):
        result = self.run_script("trust", "todo", str(self.source), FAKE_STALE_READS="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "changed")
        calls = self.calls()
        self.assertEqual(calls[:3], [f"--file update {self.source} --trust-file todo",
                                     f"--file add {self.source} --trust-file todo", "--update"])
        self.assertEqual(calls.count("--dump-db"), 3)

    def test_known_path_is_only_updated_and_reports_unchanged(self):
        (self.directory / "known").write_text(f"{self.source}\n")
        result = self.run_script("trust", "todo", str(self.source))
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "unchanged"), result.stderr)
        self.assertNotIn("add", " ".join(self.calls()))

    def test_stale_or_prefix_only_entry_fails_after_bounded_attempts(self):
        result = self.run_script("trust", "todo", str(self.source), FAKE_STALE_READS="99", TRUST_ATTEMPTS="3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not load the exact trust entries", result.stderr)
        self.assertEqual(self.calls().count("--dump-db"), 3)

    def test_inactive_fapolicyd_or_missing_file_changes_no_trust(self):
        result = self.run_script("trust", "todo", str(self.source), FAKE_INACTIVE="3")
        self.assertIn("fapolicyd is not active", result.stderr)
        result = self.run_script("trust", "todo", str(self.source), str(self.directory / "missing.py"))
        self.assertIn("not a regular file", result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_install_is_atomic_exact_and_idempotent(self):
        target = self.directory / "installed" / "tool.py"
        target.parent.mkdir()
        content = base64.b64encode(b"print('tool')\n").decode()
        first = self.run_script("install", str(target), "0644", stdin=content)
        self.assertEqual((first.returncode, first.stdout.strip()), (0, "changed"), first.stderr)
        self.assertEqual(target.read_bytes(), b"print('tool')\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        repeat = self.run_script("install", str(target), "0644", stdin=content)
        self.assertEqual(repeat.stdout.strip(), "unchanged")
        mode = self.run_script("install", str(target), "0755", stdin=content)
        self.assertEqual(mode.stdout.strip(), "changed")
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["tool.py"])

    def test_wrong_arguments_print_usage(self):
        for arguments in ((), ("trust", "todo"), ("install", "/tmp/x")):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.run_script(*arguments).returncode, 2)


if __name__ == "__main__":
    unittest.main()
