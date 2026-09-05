import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AcceptanceGuideTests(unittest.TestCase):
    def test_all_numbered_phases_have_complete_cards(self):
        guide = (ROOT / "docs/LAB-ACCEPTANCE.md").read_text()
        parts = re.split(r"^## (\d+)\. [^\n]+\n", guide, flags=re.M)
        self.assertEqual([int(n) for n in parts[1::2]], list(range(1, 12)))
        for number, body in zip(parts[1::2], parts[2::2]):
            with self.subTest(phase=number):
                for field in ("Where", "Preconditions", "Command", "PASS",
                              "Evidence", "STOP if", "Next"):
                    self.assertEqual(body.count(f"- **{field}:**"), 1)

    def test_new_run_is_not_dependent_on_development_history(self):
        quickstart = (ROOT / "docs/MANUAL-DR-QUICKSTART.md").read_text()
        self.assertIn("**NEW:**", quickstart)
        self.assertIn("**CONTINUATION:**", quickstart)
        self.assertIn("PROJECT.md is optional history", quickstart)
        guide = (ROOT / "docs/LAB-ACCEPTANCE.md").read_text()
        self.assertIn("E2E_IGNORE_HTTPS_ERRORS=false", guide)
        self.assertNotIn("fingerprint from its console", guide)
        self.assertNotIn("All services must be inactive", guide)
