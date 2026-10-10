"""E3: every app_dr_host command reaches its function with its options, and refuses a missing one.

The functions themselves have their own tests; these check only the
command line: which function a command calls, with what, what it prints,
and the exit code.
"""
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / 'installer')]
from app_dr_host import cli  # noqa: E402
from app_installer import apps  # noqa: E402

KEYCLOAK = apps.KEYCLOAK_DATABASE
FILES = SimpleNamespace(values={'TARGET_EXTERNAL_HOSTNAME': 'todo.test'})


def run(argv):
    """cli.main(argv): its exit code, its stdout as JSON (or text), and its stderr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    text = out.getvalue().strip()
    try:
        return code, json.loads(text), err.getvalue()
    except ValueError:
        return code, text, err.getvalue()


class ReplicateWorkloadTests(unittest.TestCase):
    def test_each_operation_calls_its_function_for_the_chosen_database(self):
        cases = (
            (['primary', '--node-address', '192.0.2.10'], 'configure_primary', (KEYCLOAK, '192.0.2.10'), 'changed'),
            (['rebuild-primary-check'], 'rebuild_primary_check', (KEYCLOAK,), 'changed'),
            (['quarantined'], 'require_quarantined_group', (), 'changed'),
            (['slot'], 'standby_slot', (KEYCLOAK,), 'slot'),
            (['replication-path', '--primary-address', '192.0.2.10'], 'replication_path',
             (KEYCLOAK, '192.0.2.10'), 'path'),
            (['authenticate', '--primary-address', '192.0.2.10'], 'authenticate',
             (KEYCLOAK, '192.0.2.10'), 'system_identifier'),
            (['status'], 'status', (KEYCLOAK,), 'status'),
            (['drop-slot', '--slot', 'old_standby'], 'drop_idle_slot', (KEYCLOAK, 'old_standby'), 'changed'),
        )
        for options, function, arguments, key in cases:
            with self.subTest(options[0]), patch.object(cli.replication, function, return_value='result') as called:
                code, output, _ = run(['replicate-workload', *options, '--app', KEYCLOAK.name])
                self.assertEqual(code, 0)
                called.assert_called_once_with(*arguments)
                self.assertEqual(output[key], 'result')

    def test_streaming_passes_rebuilt_and_slot(self):
        with patch.object(cli.replication, 'streaming_status', return_value='streaming') as called:
            code, output, _ = run(['replicate-workload', 'streaming', '--rebuilt', '--slot', 's1'])
        self.assertEqual((code, output), (0, {'changed': False, 'status': 'streaming'}))
        called.assert_called_once_with(apps.APPS[0].database, rebuilt=True, slot='s1')

    def test_hba_refreshes_only_on_a_primary(self):
        with patch.object(cli.replication, 'require_primary', side_effect=RuntimeError('not a primary')), \
                patch.object(cli.replication, 'refresh_hba') as refresh:
            code, _, error = run(['replicate-workload', 'hba'])
        self.assertEqual(code, 1)
        self.assertIn('app-dr-host: not a primary', error)
        refresh.assert_not_called()
        with patch.object(cli.replication, 'require_primary'), \
                patch.object(cli.replication, 'refresh_hba', return_value=True):
            self.assertEqual(run(['replicate-workload', 'hba'])[:2], (0, {'changed': True}))

    def test_drop_slot_needs_a_slot(self):
        with patch.object(cli.replication, 'drop_idle_slot') as drop:
            code, _, error = run(['replicate-workload', 'drop-slot'])
        self.assertEqual(code, 1)
        self.assertIn('drop-slot needs --slot', error)
        drop.assert_not_called()

    def test_standby_bootstraps_from_the_target_files_and_records_the_hostnames(self):
        with patch.object(cli, 'target', return_value=FILES) as target, \
                patch.object(cli.replication, 'bootstrap_standby', return_value=True) as bootstrap, \
                patch.object(cli.target_render, 'write_record') as record:
            code, output, _ = run(['replicate-workload', 'standby', '--node-address', '192.0.2.11',
                                   '--primary-address', '192.0.2.10', '--slot', 's1',
                                   '--quadlet-dir', '/q', '--target-values', '{"TARGET_EXTERNAL_HOSTNAME": "x"}'])
        self.assertEqual((code, output), (0, {'changed': True}))
        self.assertEqual(target.call_args.args[1], '192.0.2.11')
        self.assertEqual(bootstrap.call_args.args, (apps.APPS[0].database, '192.0.2.10'))
        self.assertEqual(bootstrap.call_args.kwargs['slot'], 's1')
        self.assertEqual(bootstrap.call_args.kwargs['kube_runtime_dir'], Path('/q/platform-kube-runtime'))
        self.assertIs(bootstrap.call_args.kwargs['target'], FILES)
        record.assert_called_once_with(FILES.values)

    def test_reseed_check_refuses_a_slot_or_an_image_archive(self):
        for option in (['--slot', 's1'], ['--image-archive', '/a.tar']):
            with self.subTest(option[0]), patch.object(cli, 'target', return_value=FILES), \
                    patch.object(cli.replication, 'reseed_check') as check:
                code, _, error = run(['replicate-workload', 'reseed-check', *option])
                self.assertEqual(code, 1)
                self.assertIn('registered rebuild slot', error)
                check.assert_not_called()
        with patch.object(cli, 'target', return_value=FILES), \
                patch.object(cli.replication, 'reseed_check', return_value=False) as check:
            code, output, _ = run(['replicate-workload', 'reseed-check', '--primary-address', '192.0.2.10',
                                   '--confirm-fenced', 'fenced', '--confirm-reseed', 'todo-primary'])
        self.assertEqual((code, output), (0, {'changed': False}))
        self.assertEqual(check.call_args.kwargs['confirm_reseed'], 'todo-primary')


class GroupCommandTests(unittest.TestCase):
    def test_group_commands_print_their_function_result(self):
        cases = (
            (['standby-reseed-check', '--primary-address', '192.0.2.10'], 'standby_reseed_check',
             {'changed': 'r'}),
            (['erase-standby', '--primary-address', '192.0.2.10', '--confirm-reseed', 'todo-primary'],
             'erase_standby_group', {'changed': True, 'erased': 'r'}),
            (['cluster-status', 'standby'], 'cluster_status', {'changed': False, 'status': 'r'}),
            (['require-promoted-group', '--journal', '/j.json'], 'require_promoted_group', {'changed': 'r'}),
        )
        for argv, function, expected in cases:
            with self.subTest(argv[0]), patch.object(cli.replication, function, return_value='r') as called:
                self.assertEqual(run(argv)[:2], (0, expected))
                called.assert_called_once()
        with patch.object(cli.replication, 'erase_standby_group', return_value=[]) as erase:
            run(['erase-standby', '--primary-address', '192.0.2.10', '--confirm-reseed', 'todo-primary'])
        erase.assert_called_once_with('192.0.2.10', 'todo-primary')

    def test_reseed_group_and_publish_record_the_hostnames(self):
        with patch.object(cli, 'target', return_value=FILES), \
                patch.object(cli.replication, 'reseed_group', return_value=['todo']) as reseed, \
                patch.object(cli.target_render, 'write_record') as record:
            code, output, _ = run(['reseed-group', '--primary-address', '192.0.2.10', '--confirm-fenced', 'f',
                                   '--confirm-reseed', 'todo-primary', '--node-address', '192.0.2.11'])
        self.assertEqual((code, output), (0, {'changed': True, 'reseeded': ['todo']}))
        self.assertEqual(reseed.call_args.kwargs['confirm_reseed'], 'todo-primary')
        record.assert_called_once_with(FILES.values)
        with patch.object(cli, 'target', return_value=FILES) as target, \
                patch.object(cli.replication, 'publish_primaries', return_value={'changed': True}) as publish, \
                patch.object(cli.target_render, 'write_record'):
            code, output, _ = run(['publish-primaries', 'bootstrap', '--node-address', '192.0.2.10'])
        self.assertEqual((code, output), (0, {'changed': True}))
        self.assertTrue(publish.call_args.kwargs['bootstrap'])
        # A primary keeps the hostnames it was installed with.
        self.assertIsNone(target.call_args.args[0].target_values)

    def test_deploy_promoted_and_target_values(self):
        with patch.object(cli.promoted, 'deploy', return_value=True) as deploy:
            code, output, _ = run(['deploy-promoted', '--bundle-dir', '/b', '--inventory-hostname', 'todo-standby',
                                   '--node-address', '192.0.2.11', '--quadlet-dir', '/q'])
        self.assertEqual((code, output), (0, {'changed': True}))
        self.assertEqual(deploy.call_args.kwargs['inventory_hostname'], 'todo-standby')
        self.assertEqual(deploy.call_args.kwargs['quadlet_dir'], Path('/q'))
        with patch.object(cli.target_render, 'host_hostnames', return_value={'a': 'b'}):
            self.assertEqual(run(['target-values'])[:2], (0, {'changed': False, 'values': {'a': 'b'}}))


class NginxTlsCommandTests(unittest.TestCase):
    def test_missing_options_are_refused_before_anything_runs(self):
        cases = ((['mode'], 'nginx-tls mode needs --mode'),
                 (['request', '--bundle-dir', '/b'], 'needs --bundle-dir and --node-address'),
                 (['install', '--bundle-dir', '/b', '--node-address', '192.0.2.10', '--ca', '/ca'],
                  'install needs --certificate and --ca'))
        for options, message in cases:
            with self.subTest(options[0]), patch.object(cli, 'nginx_tls') as tls:
                code, _, error = run(['nginx-tls', *options])
                self.assertEqual(code, 1)
                self.assertIn(message, error)
                self.assertFalse(tls.method_calls)

    def test_mode_sets_the_pairs_mode(self):
        with patch.object(cli.nginx_tls, 'set_mode', return_value=True) as mode:
            self.assertEqual(run(['nginx-tls', 'mode', '--mode', 'provided'])[:2], (0, {'changed': True}))
        mode.assert_called_once_with('provided')


if __name__ == '__main__':
    unittest.main()
