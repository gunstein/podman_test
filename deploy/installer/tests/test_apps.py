import dataclasses
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from todo_installer.apps import APPS  # noqa: E402


class AppRegistryTests(unittest.TestCase):
    def test_identities_are_unique_and_safe(self):
        self.assertTrue(APPS)
        for field in ('name', 'hostname', 'keycloak_client'):
            values = [getattr(app, field) for app in APPS]
            self.assertEqual(len(values), len(set(values)), field)
        for app in APPS:
            self.assertRegex(app.name, r'^[a-z][a-z0-9-]*$')
            self.assertRegex(app.chart, r'^[a-z][a-z0-9-]*$')
            self.assertRegex(app.hostname, r'^[a-z0-9.-]+$')
            self.assertTrue(re.fullmatch(r'[a-z0-9-]+', app.keycloak_client))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                app.name = 'changed'
