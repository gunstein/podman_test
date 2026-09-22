import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from todo_installer.cli import main  # noqa: E402


class CLITests(unittest.TestCase):
    def test_workload_json_and_all_overrides(self):
        for workload, function in [('postgres', 'install_postgres'),
                                   ('application', 'install_application'),
                                   ('shared-proxy', 'install_shared_proxy')]:
            with patch('todo_installer.workloads.' + function, return_value=False) as run, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['install-workload', workload, '--project-root', '/source',
                                       '--quadlet-dir', '/q', '--kube-runtime-dir', '/q/todo-kube-runtime',
                                       '--rendered-manifest-dir', '/rendered',
                                       '--postgres-publish-address', '192.0.2.1',
                                       '--publish-address', '192.0.2.2', '--service-port', '9443']), 0)
                self.assertEqual(json.loads(output.getvalue()), {'changed': False})
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                self.assertEqual(run.call_args.args, tuple(map(Path, [
                    '/source', '/q', '/q/todo-kube-runtime', '/rendered'])))
                self.assertEqual(run.call_args.kwargs['publish_address'],
                                 '192.0.2.1' if workload == 'postgres' else '192.0.2.2')

    def test_failure_has_no_json_success(self):
        with patch('todo_installer.workloads.install_postgres', side_effect=RuntimeError('failed')), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(['install-workload', 'postgres']), 1)
            self.assertEqual(output.getvalue(), '')
            self.assertIn('failed', error.getvalue())
