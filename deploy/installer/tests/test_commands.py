"""commands.run: every command has a time limit, and errors never show its output."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import commands, settings  # noqa: E402


class RunTests(unittest.TestCase):
    def test_a_hanging_command_stops_with_an_error_naming_it(self):
        with self.assertRaisesRegex(RuntimeError, r" -c timed out after 0.2 seconds") as caught:
            commands.run(sys.executable, "-c", "import time; print('secret'); time.sleep(5)", timeout=0.2)
        self.assertNotIn("secret", str(caught.exception))

    def test_the_default_limit_is_finite(self):
        self.assertEqual(commands.run.__kwdefaults__["timeout"], settings.COMMAND_TIMEOUT)
        for limit in (settings.COMMAND_TIMEOUT, settings.HEALTH_TIMEOUT, settings.IMAGE_TIMEOUT,
                      settings.DATA_COPY_TIMEOUT):
            self.assertTrue(0 < limit < 24 * 3600)


if __name__ == "__main__":
    unittest.main()
