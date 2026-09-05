import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LearningPathTests(unittest.TestCase):
    def test_current_guide_teaches_kube_not_legacy_units(self):
        guide = (ROOT / "docs/LEARNING-GUIDE.md").read_text()
        for phrase in ("helm/todo", "todo-app.service", "init container", "acceptance"):
            self.assertIn(phrase, guide)
        for phrase in ("todo-frontend.container", "todo-migrate.container",
                       "todo-frontend.service"):
            self.assertNotIn(phrase, guide)
        self.assertTrue((ROOT / "docs/legacy/LEARNING-GUIDE.md").is_file())

    def test_ui_does_not_depend_on_keycloak_sdk(self):
        app = (ROOT / "frontend/app.js").read_text()
        self.assertIn('from "./auth.js"', app)
        self.assertNotIn("keycloak", app.lower())
        image = (ROOT / "frontend/Containerfile").read_text()
        self.assertIn("frontend/auth.js frontend/keycloak-adapter.js", image)

    def test_ci_covers_adapter_and_quarantine(self):
        workflow = (ROOT / ".github/workflows/clean-install.yml").read_text()
        self.assertIn("pytest e2e/test_auth_adapter.py", workflow)
        shell_step = workflow.split("shellcheck \\", 1)[1].split("- name:", 1)[0]
        self.assertIn("scripts/todo-quarantine.sh", shell_step)
