"""What the installer renders stays as pinned in tests/fixtures/render-baseline.

A failure here means a template or the installer now renders something else.
If that change is intended, update the baseline (see tests/render_baseline.py)
and let the diff of the fixtures explain it in the same commit.
"""
import difflib
import subprocess
import unittest

import render_baseline


class RenderBaselineTests(unittest.TestCase):
    def test_the_same_files_are_rendered(self):
        self.assertEqual(sorted(render_baseline.rendered()), sorted(render_baseline.kept()))

    def test_git_keeps_every_baseline_file(self):
        # A fixture Git ignores is missing from a clean checkout, where this baseline would then fail.
        names = [str(render_baseline.FIXTURES / name) for name in render_baseline.kept()]
        ignored = subprocess.run(["git", "check-ignore", *names], cwd=render_baseline.ROOT,
                                 capture_output=True, text=True, check=False).stdout.split()
        self.assertEqual(ignored, [])

    def test_every_file_renders_the_same_bytes(self):
        kept = render_baseline.kept()
        for name, content in render_baseline.rendered().items():
            with self.subTest(file=name):
                if name in kept and content != kept[name]:
                    diff = difflib.unified_diff(kept[name].decode().splitlines(), content.decode().splitlines(),
                                                "baseline/" + name, "rendered/" + name, lineterm="")
                    self.fail("\n".join(list(diff)[:60]))


if __name__ == "__main__":
    unittest.main()
