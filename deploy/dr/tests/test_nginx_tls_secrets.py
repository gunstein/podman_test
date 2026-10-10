"""nginx_tls with Podman secrets: each DR host's nginx certificate, with real openssl.

The host is the installer tests' SecretHost (test_tls_secrets.py): it keeps
the secrets and runs every throwaway proxy container here. The CA is
deploy/scripts/app_ca.py. test_nginx_tls.py keeps the same steps working
with the TLS volume.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve()
sys.path[:0] = [str(HERE.parents[1]), str(HERE.parents[2] / 'installer'), str(HERE.parents[2] / 'installer/tests')]
import dr_target  # noqa: E402
from app_dr_host import cli, nginx_tls, promoted  # noqa: E402
from app_installer import apps, target_render, tls_secrets  # noqa: E402
from test_tls_secrets import REAL_RUN, SecretHost, app_ca, kube_files  # noqa: E402

RECORD = {'TARGET_IDENTITY_HOSTNAME': 'todo.example.test', 'TARGET_NOTES_HOSTNAME': 'notes.example.test'}
NAMES = ['todo.example.test', 'notes.example.test']
SECRET = apps.PROXY_TLS_SECRETS


class NginxTlsSecretTest(unittest.TestCase):
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
        self.host = SecretHost()
        self.host.__enter__()
        self.addCleanup(self.host.__exit__)
        self.host.record.parent.mkdir(parents=True)
        self.host.record.write_text(json.dumps(RECORD))
        self.loaded = []
        for target, name, value in (
                (nginx_tls, 'PAIR_MODE', self.directory / 'nginx-tls-mode'),
                (nginx_tls.images, 'prepare_shared', lambda *args: self.loaded.append(args) or {'proxy': False})):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def where(self):
        return dr_target.bundle(), '/home/todo/platform-offline-m12', '192.0.2.11'

    def sign(self, csr, days=365):
        request, output = self.directory / 'host.csr', self.directory / f'host-{days}.crt'
        request.write_text(csr)
        with patch('subprocess.run', REAL_RUN):
            app_ca.sign(self.ca, request, output, days, passphrase_file=self.passphrase)
        return output

    def install(self, days=365):
        csr, _names = nginx_tls.request(*self.where())
        return nginx_tls.install(*self.where(), self.sign(csr, days), self.ca / 'ca.crt')


class StandbyTests(NginxTlsSecretTest):
    def test_a_standby_gets_only_the_proxy_image_and_keeps_its_waiting_key_in_a_secret(self):
        csr, names = nginx_tls.request(*self.where())
        self.assertEqual(names, NAMES)
        self.assertEqual(self.loaded, [('/home/todo/platform-offline-m12', 'offline', '/home/todo/platform-offline-m12')])
        # No TLS volume: nothing is played, nothing is mounted.
        self.assertFalse(self.host.ran('podman', 'kube', 'play'))
        self.assertFalse([argv for argv in self.host.ran('podman', 'run') if '--volume' in argv])
        self.assertIn('PRIVATE KEY', self.host.secrets[SECRET['request.key']])
        self.assertNotIn('PRIVATE KEY', csr)

    def test_a_signed_certificate_installs_without_a_running_nginx(self):
        self.assertTrue(self.install())
        self.assertEqual(kube_files(self.host)['tls-mode'], 'provided\n')
        self.assertFalse(self.host.ran('systemctl', '--user', 'restart'))

    def test_a_host_without_recorded_hostnames_is_refused(self):
        target_render.record_path().unlink()
        with self.assertRaisesRegex(tls_secrets.TlsError, 'records no public hostnames'):
            nginx_tls.request(*self.where())


class PairModeTests(NginxTlsSecretTest):
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

    def test_a_certificate_that_no_longer_fits_or_nears_its_end_fails_the_check(self):
        nginx_tls.set_mode('provided')
        self.install(days=20)
        self.assertRegex(nginx_tls.readiness()[1][0], r'expires in 1[89] days')
        target_render.record_path().write_text(json.dumps({**RECORD, 'TARGET_NOTES_HOSTNAME': 'other.example.test'}))
        self.assertIn('not valid for other.example.test', nginx_tls.readiness()[1][0])


class PromotedTests(NginxTlsSecretTest):
    def deploy(self, steps):
        with patch.object(promoted, 'require_identity', lambda *args: None), \
                patch.object(promoted.target_render, 'load_on_host',
                             return_value=dr_target.load('192.0.2.11', **RECORD)), \
                patch.object(promoted.replication, 'require_promoted_group', lambda journal: None), \
                patch.object(promoted, 'exists', lambda kind, name: True), \
                patch.object(promoted.images, 'prepare_offline_group', lambda bundle: steps.append('images')), \
                patch.object(promoted, 'install_workloads', lambda *args: steps.append('workloads')
                             or (_ for _ in ()).throw(RuntimeError('stop after the workloads'))):
            promoted.deploy(project_root=dr_target.bundle(), quadlet_dir=self.directory / 'systemd',
                            bundle_dir='/bundle', inventory_hostname='todo-standby', node_address='192.0.2.11',
                            journal=self.directory / 'promotion.json', config_dir=self.directory,
                            service_port=8443)

    def test_a_local_pair_gets_its_own_demo_ca_before_the_workloads(self):
        steps = []
        with self.assertRaisesRegex(RuntimeError, 'stop after the workloads'):
            self.deploy(steps)
        self.assertEqual(steps, ['images', 'workloads'])
        files = kube_files(self.host)
        self.assertEqual(files['tls-mode'], 'local\n')
        self.assertIn('BEGIN CERTIFICATE', files['server.crt'])

    def test_a_provided_pair_keeps_its_certificate_and_refuses_without_one(self):
        nginx_tls.set_mode('provided')
        steps = []
        with self.assertRaisesRegex(RuntimeError, 'install this host\'s certificate first'):
            self.deploy(steps)
        self.assertEqual(steps, ['images'])
        self.install()
        before = kube_files(self.host)
        steps = []
        with self.assertRaisesRegex(RuntimeError, 'stop after the workloads'):
            self.deploy(steps)
        self.assertEqual(kube_files(self.host), before)
        self.assertNotIn(SECRET['ca.key'], self.host.secrets)


class CommandTests(NginxTlsSecretTest):
    def main(self, *argv):
        with patch('sys.stdout') as stdout, patch('sys.stderr'):
            code = cli.main(list(argv))
        return code, ''.join(call.args[0] for call in stdout.write.call_args_list)

    def test_request_install_and_mode_from_the_command_line(self):
        root, bundle, address = self.where()
        options = ['--project-root', str(root), '--bundle-dir', bundle, '--node-address', address]
        code, printed = self.main('nginx-tls', 'request', *options)
        self.assertEqual(code, 0)
        certificate = self.sign(json.loads(printed)['request'])
        code, printed = self.main('nginx-tls', 'install', *options, '--certificate', str(certificate),
                                  '--ca', str(self.ca / 'ca.crt'))
        self.assertEqual((code, json.loads(printed)), (0, {'changed': True}))
        self.assertEqual(self.main('nginx-tls', 'mode', '--mode', 'provided'), (0, '{"changed": true}\n'))


if __name__ == '__main__':
    unittest.main()
