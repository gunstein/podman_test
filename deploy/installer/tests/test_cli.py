import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer.cli import main  # noqa: E402


class CLITests(unittest.TestCase):
    def test_install_prints_one_json_result(self):
        with patch('app_installer.install.install', return_value=False) as install, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['install', '--project-root', '/source', '--publish-address', '192.0.2.2',
                                   '--service-port', '9443']), 0)
        self.assertEqual(output.getvalue(), '{"changed": false}\n')
        self.assertEqual(install.call_args.args[0], Path('/source'))
        self.assertEqual(install.call_args.args[5:7], ('192.0.2.2', 9443))

    def test_failure_has_no_json_success(self):
        with patch('app_installer.install.install', side_effect=RuntimeError('failed')), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(['install']), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(error.getvalue(), 'app-installer: failed\n')

    def test_only_the_single_host_commands_and_the_registry_remain(self):
        # The DR tools import the installer's functions; nothing runs workload commands.
        for command in ('install-workload', 'configure-clients', 'services'):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                main([command])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['replication-apps']), 0)
        self.assertEqual(json.loads(output.getvalue()), ['todo', 'notes', 'keycloak'])
