"""L1: one journald line per operations command, never with an argument's value."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import cli, oplog  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class OplogTests(unittest.TestCase):
    def setUp(self):
        oplog.describe()
        self.addCleanup(oplog.describe)
        self.lines = []
        patchers = (patch.object(oplog.shutil, 'which', return_value='/usr/bin/logger'),
                    patch.object(oplog.subprocess, 'run',
                                 side_effect=lambda argv, **_: self.lines.append(argv)))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_command_logs_its_name_choices_exit_and_time_only(self):
        def main():
            oplog.describe('replicate-workload', 'standby', None, 'notes')
            return 0
        self.assertEqual(oplog.run('app-dr-host', main), 0)
        [argv] = self.lines
        self.assertEqual(argv[:4], ['logger', '--tag', 'app-dr-host', '--'])
        self.assertRegex(argv[4], r'^replicate-workload standby notes exit=0 seconds=\d+\.\d$')

    def test_a_real_main_names_its_command_but_not_its_values(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(oplog.run('app-installer', lambda: cli.main(['backup', 'nightly', '--keep-days', '0'])), 1)
        self.assertRegex(self.lines[0][4], r'^backup nightly exit=1 ')
        self.assertNotIn('--keep-days', self.lines[0][4])

    def test_help_and_usage_errors_log_nothing(self):
        for argv in (['--help'], ['no-such-command']):
            with self.subTest(argv), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                oplog.run('app-installer', lambda argv=argv: cli.main(argv))
        self.assertEqual(self.lines, [])

    def test_a_failure_that_escapes_is_logged_as_interrupted(self):
        def main():
            oplog.describe('failover')
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            oplog.run('app-ops', main)
        self.assertRegex(self.lines[0][4], r'^failover exit=interrupted ')

    def test_app_ops_keeps_what_it_printed_in_one_file_per_run(self):
        def main():
            oplog.describe('failover')
            print('{"changed": true}')
            print('app-ops failover: promote done', file=sys.stderr)
            return 0
        with tempfile.TemporaryDirectory() as temp, patch.object(oplog, 'KEPT', Path(temp) / 'app-ops'), \
                contextlib.redirect_stdout(io.StringIO()) as shown, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(oplog.run('app-ops', main, keep=True), 0)
            [path] = (Path(temp) / 'app-ops').iterdir()
            self.assertRegex(path.name, r'^\d{8}T\d{6}Z-failover\.log$')
            text = path.read_text()
        self.assertEqual(shown.getvalue(), '{"changed": true}\n', 'the terminal still gets it')
        self.assertIn('{"changed": true}\napp-ops failover: promote done\n# failover exit=0 ', text)
        self.assertIn(f' log={path}', self.lines[0][4])

    def test_every_entry_point_runs_its_main_through_oplog(self):
        for path, call in (('deploy/installer/app_installer/__main__.py', "oplog.run('app-installer', main)"),
                           ('deploy/dr/app_dr_host/__main__.py', "oplog.run('app-dr-host', main)"),
                           ('deploy/dr/app_ops/__main__.py', "oplog.run('app-ops', main, keep=True)"),
                           ('deploy/dr/scripts/app_dr.py', "oplog.run('app-dr', main)"),
                           ('deploy/dr/scripts/app_backup.py', 'oplog.run("app-backup", main)')):
            with self.subTest(path):
                self.assertIn(call, (ROOT / path).read_text())


if __name__ == '__main__':
    unittest.main()
