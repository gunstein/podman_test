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
    def test_workload_json_and_all_overrides(self):
        for workload, function in [('postgres', 'install_postgres'),
                                   ('application', 'install_application'),
                                   ('shared-proxy', 'install_shared_proxy')]:
            with patch('app_installer.workloads.' + function, return_value=False) as run, \
                    patch('app_installer.workloads.install_keycloak', return_value=False) as identity, \
                    patch('app_installer.install.preflight'), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['install-workload', workload, '--project-root', '/source',
                                       '--quadlet-dir', '/q', '--kube-runtime-dir', '/q/todo-kube-runtime',
                                       '--rendered-manifest-dir', '/rendered',
                                       '--postgres-publish-address', '192.0.2.1',
                                       '--publish-address', '192.0.2.2', '--service-port', '9443']), 0)
                self.assertEqual(identity.call_count, int(workload == 'application'))
                self.assertEqual(json.loads(output.getvalue()), {'changed': False})
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                self.assertEqual(run.call_args.args, tuple(map(Path, [
                    '/source', '/q', '/q/todo-kube-runtime', '/rendered'])))
                self.assertEqual(run.call_args.kwargs['publish_address'],
                                 '192.0.2.1' if workload == 'postgres' else '192.0.2.2')

    def test_failure_has_no_json_success(self):
        with patch('app_installer.workloads.install_postgres', side_effect=RuntimeError('failed')), \
                patch('app_installer.install.preflight'), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(['install-workload', 'postgres']), 1)
            self.assertEqual(output.getvalue(), '')
            self.assertIn('failed', error.getvalue())

    def test_identity_has_its_own_workload_command(self):
        with patch('app_installer.workloads.install_keycloak', return_value=True) as identity, \
                patch('app_installer.install.preflight'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['install-workload', 'keycloak']), 0)
            self.assertEqual(identity.call_count, 1)
            self.assertEqual(json.loads(output.getvalue()), {'changed': True})

    def test_dr_application_preserves_identity_change_result(self):
        with patch('app_installer.workloads.install_application', return_value=False), \
                patch('app_installer.workloads.install_keycloak', return_value=True), \
                patch('app_installer.install.preflight'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['install-workload', 'application']), 0)
            self.assertEqual(json.loads(output.getvalue()), {'changed': True})

    def test_notes_application_does_not_reinstall_shared_identity(self):
        with patch('app_installer.workloads.install_application', return_value=False) as application, \
                patch('app_installer.workloads.install_keycloak') as identity, \
                patch('app_installer.install.preflight'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['install-workload', 'application', '--app', 'notes']), 0)
            self.assertEqual(application.call_args.kwargs['app'].name, 'notes')
            identity.assert_not_called()
            self.assertEqual(json.loads(output.getvalue()), {'changed': False})

    def test_preflight_runs_before_the_selected_workload_installs(self):
        with patch('app_installer.install.preflight') as preflight, \
                patch('app_installer.workloads.install_postgres', return_value=False) as postgres, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['install-workload', 'postgres', '--quadlet-dir', '/q']), 0)
            preflight.assert_called_once_with(Path('/q'))
            postgres.assert_called_once()
