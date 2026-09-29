"""Values that code outside app_installer/settings.py must repeat, kept in step with it."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/installer"))

from app_installer import settings  # noqa: E402


def tracked_files(*patterns):
    """The files Git tracks under these patterns, outside the frozen history."""
    names = subprocess.check_output(["git", "ls-files", *patterns], cwd=ROOT, text=True).split()
    return [ROOT / name for name in names if not name.startswith("docs/history/")]


class BundleNameTests(unittest.TestCase):
    def test_every_script_and_guide_names_the_bundle_of_the_current_image_tag(self):
        # build-bundle.sh reads the tag from settings; the guides spell the
        # name out, so a new IMAGE_TAG must take them along.
        expected = f"todo-offline-{settings.IMAGE_TAG}"
        stale = [f"{path.relative_to(ROOT)}: {name}"
                 for path in tracked_files("*.md", "*.sh", "*.py", "*.yml")
                 for name in set(re.findall(r"todo-offline-m\d+", path.read_text()))
                 if name != expected]
        self.assertEqual(stale, [])

    def test_the_bundle_builder_takes_the_tag_from_settings(self):
        builder = (ROOT / "deploy/offline/build-bundle.sh").read_text()
        self.assertIn("print(settings.IMAGE_TAG)", builder)
        self.assertNotRegex(builder, r"todo-offline-m\d+")


class ToolsLayoutTests(unittest.TestCase):
    """/opt/todo/{bin,lib} is fixed: the tools find their packages before settings can be read."""

    def test_lib_lies_next_to_bin(self):
        self.assertEqual((settings.TOOLS_BIN.name, settings.TOOLS_LIB.name), ("bin", "lib"))
        self.assertEqual(settings.TOOLS_BIN.parent, settings.TOOLS_LIB.parent)

    def test_the_dr_scripts_look_in_the_lib_next_to_their_bin(self):
        for script in ("app_dr.py", "app_backup.py"):
            with self.subTest(script=script):
                text = (ROOT / "deploy/dr/scripts" / script).read_text()
                self.assertIn("sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))", text)

    def test_the_quarantine_helper_names_the_same_lib(self):
        helper = (ROOT / "deploy/dr/scripts/app-quarantine.sh").read_text()
        self.assertIn(f"PYTHONPATH={settings.TOOLS_LIB} ", helper)
