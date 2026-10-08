"""nginx_tls: each DR host's nginx certificate from the organisation's CA, with real openssl.

The Podman fake and the CA come from the installer's tests
(test_tls.FakePodman, deploy/scripts/app_ca.py): every openssl step runs here
against a directory that plays the TLS volume. The standby's TLS volume is
created from the real bundle's claim (dr_target).
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path[:0] = [str(HERE.parents[1]), str(HERE.parents[2] / 'installer'), str(HERE.parents[2] / 'installer/tests')]
import dr_target  # noqa: E402
from app_dr_host import cli, nginx_tls, promoted  # noqa: E402
from app_installer import target_render, tls  # noqa: E402
from test_tls import REAL_RUN, ROOT, FakePodman, app_ca  # noqa: E402

RECORD = {'TARGET_EXTERNAL_HOSTNAME': 'todo.example.test', 'TARGET_NOTES_HOSTNAME': 'notes.example.test'}
NAMES = ['todo.example.test', 'notes.example.test']


class StandbyPodman(FakePodman):
    """A standby: no TLS volume until the bundle's claim is played, which this fake then creates."""

    def __init__(self, test, volume):
        super().__init__(test, volume)
        self.volume_exists = False
        self.claims = []

    def __call__(self, argv, input=None, timeout=None, **kwargs):
        if list(argv[:4]) == ['podman', 'kube', 'play', '-']:
            self.claims.append(input)
            self.volume_exists = True
            return subprocess.CompletedProcess(argv, 0, '', '')
        return super().__call__(argv, input=input, timeout=timeout, **kwargs)


class NginxTlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = Path(tempfile.mkdtemp())
        cls.addClassCleanup(lambda: REAL_RUN(['rm', '-rf', str(cls.shared)], check=True))
        cls.passphrase = cls.shared / 'passphrase'
        cls.passphrase.write_text('a test passphrase\n')
        cls.ca = cls.shared / 'ca'
        app_ca.init(cls.ca, ['example.test'], 'Test root CA', passphrase_file=cls.passphrase)

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: REAL_RUN(['rm', '-rf', str(self.directory)], check=True))
        self.volume = self.directory / 'volume'
        self.volume.mkdir()
        self.podman = StandbyPodman(self, self.volume)
        record = self.directory / 'config/target-values.json'
        record.parent.mkdir()
        record.write_text(json.dumps(RECORD))
        self.loaded = []
        for target, name, value in (
                (target_render, 'record_path', lambda: record),
                (nginx_tls, 'PAIR_MODE', self.directory / 'config/nginx-tls-mode'),
                (tls, 'ENTRYPOINT', str(ROOT / 'proxy/proxy-entrypoint.sh')),
                (tls, 'KEY', 'rsa:2048'),
                (nginx_tls.images, 'prepare_shared', lambda *args: self.loaded.append(args) or {'proxy': False})):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def where(self):
        return dr_target.bundle(), '/home/todo/todo-offline-m12', '192.0.2.11'

    def certificate(self, days=365):
        csr, names = nginx_tls.request(*self.where())
        request = self.directory / 'host.csr'
        request.write_text(csr)
        output = self.directory / f'host-{days}.crt'
        app_ca.sign(self.ca, request, output, days, passphrase_file=self.passphrase)
        return output, names

    def install(self, days=365):
        certificate, _ = self.certificate(days)
        return nginx_tls.install(*self.where(), certificate, self.ca / 'ca.crt')


class StandbyTests(NginxTlsTest):
    def test_a_standby_gets_the_image_and_the_bundles_tls_volume_before_its_request(self):
        csr, names = nginx_tls.request(*self.where())
        self.assertEqual(names, NAMES)
        self.assertEqual(app_ca.requested_names(self.write(csr)), NAMES)
        self.assertEqual(self.loaded, [('/home/todo/todo-offline-m12', 'offline', '/home/todo/todo-offline-m12')])
        self.assertEqual(len(self.podman.claims), 1)
        self.assertIn('name: todo-nginx-data', self.podman.claims[0])
        self.assertIn('volume.podman.io/uid', self.podman.claims[0])
        self.assertNotIn('kind: Pod', self.podman.claims[0])
        # The volume exists now: the next request plays nothing.
        nginx_tls.request(*self.where())
        self.assertEqual(len(self.podman.claims), 1)

    def write(self, text):
        path = self.directory / 'request.csr'
        path.write_text(text)
        return path

    def test_a_signed_certificate_installs_without_a_running_nginx(self):
        self.assertTrue(self.install())
        self.assertEqual(self.podman.file(tls.MODE_FILE), 'provided\n')
        self.assertFalse([call for call in self.podman.calls if 'reload' in call])

    def test_a_host_without_recorded_hostnames_is_refused(self):
        target_render.record_path().unlink()
        with self.assertRaisesRegex(tls.TlsError, 'records no public hostnames'):
            nginx_tls.request(*self.where())


class PairModeTests(NginxTlsTest):
    def test_the_mode_is_local_until_app_ops_sets_it(self):
        self.assertEqual(nginx_tls.pair_mode(), 'local')
        self.assertTrue(nginx_tls.set_mode('provided'))
        self.assertFalse(nginx_tls.set_mode('provided'))
        self.assertEqual(nginx_tls.pair_mode(), 'provided')
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            nginx_tls.set_mode('custom')

    def test_a_local_pair_checks_nothing(self):
        self.assertEqual(nginx_tls.readiness(), ([], []))
        nginx_tls.require_for_failover()
        self.assertEqual(self.podman.calls, [])

    def test_a_provided_pair_needs_a_fitting_certificate_on_this_host(self):
        nginx_tls.set_mode('provided')
        _lines, problems = nginx_tls.readiness()
        self.assertIn('nginx could not start here with your CA\'s certificate', problems[0])
        with self.assertRaisesRegex(RuntimeError, 'new demo CA that no client trusts'):
            nginx_tls.require_for_failover()
        self.install()
        lines, problems = nginx_tls.readiness()
        self.assertEqual(problems, [])
        self.assertRegex(lines[0], r'nginx certificate from your CA: valid 36[45] more days')
        nginx_tls.require_for_failover()

    def test_a_certificate_that_no_longer_fits_the_recorded_names_fails(self):
        self.install()
        nginx_tls.set_mode('provided')
        record = target_render.record_path()
        record.write_text(json.dumps({**RECORD, 'TARGET_NOTES_HOSTNAME': 'other.example.test'}))
        _lines, problems = nginx_tls.readiness()
        self.assertIn('not valid for other.example.test', problems[0])
        with self.assertRaisesRegex(RuntimeError, 'not valid for other.example.test'):
            nginx_tls.require_for_failover()

    def test_thirty_days_before_the_end_the_check_fails(self):
        nginx_tls.set_mode('provided')
        self.install(days=40)
        _lines, problems = nginx_tls.readiness()
        self.assertEqual(problems, [])
        self.install(days=20)
        _lines, problems = nginx_tls.readiness()
        self.assertRegex(problems[0], r'expires in 1[89] days')


class CommandTests(NginxTlsTest):
    def main(self, *argv):
        with patch('sys.stdout') as stdout, patch('sys.stderr') as stderr:
            code = cli.main(list(argv))
        printed = ''.join(call.args[0] for call in stdout.write.call_args_list)
        errors = ''.join(call.args[0] for call in stderr.write.call_args_list)
        return code, printed, errors

    def test_request_install_and_mode_from_the_command_line(self):
        root, bundle, address = self.where()
        options = ['--project-root', str(root), '--bundle-dir', bundle, '--node-address', address]
        code, printed, _ = self.main('nginx-tls', 'request', *options)
        self.assertEqual(code, 0)
        result = json.loads(printed)
        self.assertEqual(result['hostnames'], NAMES)
        request = self.directory / 'cli.csr'
        request.write_text(result['request'])
        certificate = self.directory / 'cli.crt'
        app_ca.sign(self.ca, request, certificate, 365, passphrase_file=self.passphrase)
        code, printed, _ = self.main('nginx-tls', 'install', *options, '--certificate', str(certificate),
                                     '--ca', str(self.ca / 'ca.crt'))
        self.assertEqual((code, json.loads(printed)), (0, {'changed': True}))
        code, printed, _ = self.main('nginx-tls', 'mode', '--mode', 'provided')
        self.assertEqual((code, json.loads(printed)), (0, {'changed': True}))

    def test_missing_options_are_one_error_line(self):
        self.assertEqual(self.main('nginx-tls', 'request')[0], 1)
        code, _, errors = self.main('nginx-tls', 'mode')
        self.assertEqual(code, 1)
        self.assertIn('app-dr-host: nginx-tls mode needs --mode', errors)


class FailoverGateTests(NginxTlsTest):
    def test_deploy_promoted_refuses_a_provided_pair_without_a_certificate_before_any_workload_changes(self):
        nginx_tls.set_mode('provided')
        steps = []
        with patch.object(promoted, 'require_identity', lambda *args: None), \
                patch.object(promoted.target_render, 'load_on_host',
                             return_value=dr_target.load('192.0.2.11', **RECORD)), \
                patch.object(promoted.replication, 'require_promoted_group', lambda journal: None), \
                patch.object(promoted, 'exists', lambda kind, name: True), \
                patch.object(promoted.images, 'prepare_offline_group', lambda bundle: steps.append('images')), \
                patch.object(promoted, 'install_workloads', lambda *args: steps.append('workloads')), \
                self.assertRaisesRegex(RuntimeError, 'install this host\'s certificate first'):
            promoted.deploy(project_root=dr_target.bundle(), quadlet_dir=self.directory / 'systemd',
                            bundle_dir='/bundle', inventory_hostname='todo-standby', node_address='192.0.2.11',
                            journal=self.directory / 'promotion.json', config_dir=self.directory,
                            service_port=8443)
        # The images may load (the check needs the proxy image); no workload is touched.
        self.assertEqual(steps, ['images'])


if __name__ == '__main__':
    unittest.main()
