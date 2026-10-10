"""tls_secrets.py: nginx's certificate as Podman secrets, with real openssl and a Podman that runs it here.

FakeHost keeps the secrets and runs each throwaway proxy container
(tls_secrets.proxy) as a local shell, with every --secret written as a file
where the container would see it (fake_host.proxy_container). Certificates
from an organisation's CA come from deploy/scripts/app_ca.py, run on this
host from its own directory.
"""
import base64
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, cli, settings, tls_secrets  # noqa: E402
from fake_host import REAL_RUN, FakeHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('app_ca', ROOT / 'deploy/scripts/app_ca.py')
assert SPEC and SPEC.loader
app_ca = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_ca)
NAMES = ['todo.example.test', 'notes.example.test']
SECRET = apps.PROXY_TLS_SECRETS


class SecretHost(FakeHost):
    """A FakeHost whose nginx runs under systemd (or not), and serves what its Kube secret held at its start."""

    def __init__(self, nginx='stopped'):
        super().__init__()
        self.nginx = nginx  # 'stopped', 'systemd' or 'development'
        self.serving = None  # the server.crt nginx read when it last started

    def answer(self, argv, input):
        if argv[:3] == ['systemctl', '--user', 'is-active'] and argv[3] == tls_secrets.SERVICE:
            return (0, 'active\n') if self.nginx == 'systemd' else (3, 'inactive\n')
        if argv[:3] == ['systemctl', '--user', 'restart']:
            self.serving = kube_files(self)['server.crt']
            return 0, ''
        if argv[:3] == ['podman', 'container', 'exists']:
            return int(self.nginx == 'stopped'), ''
        if argv[:2] == ['podman', 'exec'] and 'served' in argv:
            return 0, fingerprint(self.serving) if self.serving else ''
        return super().answer(argv, input)


def kube_files(host):
    """The files nginx would see: the Kube secret's data, decoded."""
    data = json.loads(host.secrets[apps.PROXY_KUBE_TLS_SECRET])['data']
    return {name: base64.b64decode(value).decode() for name, value in data.items()}


def openssl(*arguments, cwd, check=True):
    return REAL_RUN(['openssl', *map(str, arguments)], cwd=cwd, text=True, capture_output=True, check=check)


def fingerprint(certificate):
    return REAL_RUN(['openssl', 'x509', '-noout', '-fingerprint', '-sha256'], input=certificate, text=True,
                    capture_output=True, check=True).stdout.strip()


class SecretTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One organisation CA for the class, as in test_tls.py.
        cls.shared = Path(tempfile.mkdtemp())
        cls.addClassCleanup(lambda: REAL_RUN(['rm', '-rf', str(cls.shared)], check=True))
        cls.passphrase = cls.shared / 'passphrase'
        cls.passphrase.write_text('a test passphrase\n')
        cls.ca = cls.shared / 'ca'
        app_ca.init(cls.ca, ['example.test'], 'Test root CA', passphrase_file=cls.passphrase)

    def setUp(self, nginx='stopped'):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: REAL_RUN(['rm', '-rf', str(self.directory)], check=True))
        self.host = SecretHost(nginx)
        self.host.__enter__()
        self.addCleanup(self.host.__exit__)
        self.host.record.parent.mkdir(parents=True)
        self.host.record.write_text(json.dumps({'TARGET_IDENTITY_HOSTNAME': NAMES[0],
                                                'TARGET_NOTES_HOSTNAME': NAMES[1]}))

    def secret(self, name):
        return self.host.secrets.get(SECRET[name])

    def sign(self, request, days=365, ca=None):
        """The organisation's CA signs request; app_ca runs this machine's openssl, not the fake's."""
        output = self.directory / f'signed-{len(list(self.directory.glob("signed-*")))}.crt'
        with patch('subprocess.run', REAL_RUN):
            app_ca.sign(ca or self.ca, request, output, days, passphrase_file=self.passphrase)
        return output

    def request(self, new_key=False, hostnames=None):
        output = self.directory / 'host.csr'
        tls_secrets.request(output, new_key, hostnames)
        return output

    def install(self, certificate, ca=None, hostnames=None):
        return tls_secrets.install(certificate, ca or self.ca / 'ca.crt', hostnames)

    def created(self):
        return [argv for argv in self.host.ran('podman', 'secret', 'create')]


class LocalModeTests(SecretTest):
    def test_the_demo_ca_and_leaf_are_secrets_and_nginx_gets_only_what_it_serves(self):
        self.assertTrue(tls_secrets.provision(NAMES))
        self.assertEqual(self.secret('tls-mode'), 'local\n')
        files = kube_files(self.host)
        self.assertEqual(list(files), ['tls-mode', 'ca.crt', 'server.crt', 'server.key'])
        self.assertEqual(files['server.key'], self.secret('server.key'))
        self.assertNotIn(self.secret('ca.key').strip(), json.dumps(files))
        with tempfile.TemporaryDirectory() as directory:
            for name, text in files.items():
                Path(directory, name).write_text(text)
            for name in NAMES:
                self.assertEqual(openssl('verify', '-CAfile', 'ca.crt', '-verify_hostname', name, 'server.crt',
                                         cwd=directory, check=False).returncode, 0)
            self.assertIn('CN=todo.example.test',
                          openssl('x509', '-in', 'server.crt', '-noout', '-subject', cwd=directory).stdout
                          .replace(' ', ''))

    def test_every_key_comes_from_a_container_on_stdout_and_never_from_a_file_on_the_host(self):
        tls_secrets.provision(NAMES)
        for argv in self.host.ran('podman', 'run'):
            self.assertEqual(argv[argv.index('--network') + 1], 'none')
            self.assertEqual(argv[argv.index('--user') + 1], '101:101')
            self.assertIn('--tmpfs', argv)
            self.assertNotIn('--volume', argv)
            self.assertIn(apps.PROXY_IMAGE, argv)
        # A key goes from stdout straight into `podman secret create NAME -`.
        for argv in self.created():
            self.assertEqual(argv[-1], '-')
        # The secrets come in as files, readable by the nginx user only.
        mounts = [argv[i + 1] for argv in self.host.ran('podman', 'run') for i, word in enumerate(argv)
                  if word == '--secret']
        self.assertIn(f'{SECRET["ca.key"]},type=mount,target=/run/platform-tls/ca.key,uid=101,gid=101,mode=0400', mounts)

    def test_a_repeat_changes_nothing(self):
        tls_secrets.provision(NAMES)
        before = dict(self.host.secrets)
        self.host.calls.clear()
        self.assertFalse(tls_secrets.provision(NAMES))
        self.assertEqual(self.created(), [])
        self.assertEqual(self.host.secrets, before)

    def test_a_new_hostname_gives_a_new_leaf_from_the_same_ca(self):
        tls_secrets.provision(NAMES[:1])
        ca, leaf = self.secret('ca.crt'), self.secret('server.crt')
        self.host.calls.clear()
        self.assertTrue(tls_secrets.provision(NAMES))
        self.assertEqual(self.secret('ca.crt'), ca)
        self.assertNotEqual(self.secret('server.crt'), leaf)
        self.assertIn(['podman', 'secret', 'create', '--replace', apps.PROXY_KUBE_TLS_SECRET, '-'], self.created())

    def test_a_leaf_or_a_ca_near_its_end_is_replaced(self):
        with patch.object(tls_secrets, 'LEAF_DAYS', 10):
            tls_secrets.provision(NAMES)
        ca, leaf = self.secret('ca.crt'), self.secret('server.crt')
        self.assertTrue(tls_secrets.provision(NAMES))
        self.assertEqual(self.secret('ca.crt'), ca)
        self.assertNotEqual(self.secret('server.crt'), leaf)
        with patch.object(tls_secrets, 'CA_DAYS', 10):
            self.host.secrets.pop(SECRET['ca.key'])
            tls_secrets.provision(NAMES)
        ca = self.secret('ca.crt')
        self.assertTrue(tls_secrets.provision(NAMES))
        self.assertNotEqual(self.secret('ca.crt'), ca)

    def test_a_host_that_served_from_the_tls_volume_keeps_its_ca_and_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('subprocess.run', REAL_RUN):
                openssl('req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '3650', '-keyout', 'ca.key',
                        '-out', 'ca.crt', '-subj', '/CN=Todo Demo Local Root CA',
                        '-addext', 'basicConstraints=critical,CA:TRUE', cwd=directory)
                openssl('req', '-new', '-newkey', 'rsa:2048', '-noenc', '-keyout', 'server.key',
                        '-out', 'server.csr', '-subj', f'/CN={NAMES[0]}', cwd=directory)
                Path(directory, 'ext').write_text('subjectAltName=' + ','.join(f'DNS:{n}' for n in NAMES) + '\n')
                openssl('x509', '-req', '-in', 'server.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-set_serial', '1',
                        '-days', '300', '-extfile', 'ext', '-out', 'server.crt', cwd=directory)
            volume = {name: Path(directory, name).read_text() for name in ('ca.key', 'ca.crt', 'server.key',
                                                                            'server.crt')}
        self.host.volumes.add('platform-nginx-data')
        self.host.volume_files.update(volume)
        self.assertTrue(tls_secrets.provision(NAMES))
        for name, text in volume.items():
            self.assertEqual(self.secret(name), text)
        self.assertEqual(kube_files(self.host)['ca.crt'], volume['ca.crt'])
        self.assertEqual(self.secret('tls-mode'), 'local\n')
        # Copied once: the next run neither reads the volume nor issues anything.
        self.host.calls.clear()
        self.assertFalse(tls_secrets.provision(NAMES))
        self.assertFalse([argv for argv in self.host.ran('podman', 'run') if '--volume' in argv])

    def test_an_old_proxy_image_is_refused_before_anything(self):
        with patch.object(self.host, 'answer', side_effect=lambda argv, input: (0, '[{"Labels":{}}]')
                          if argv[:3] == ['podman', 'image', 'inspect'] else SecretHost.answer(self.host, argv, input)):
            with self.assertRaisesRegex(tls_secrets.TlsError, 'does not know provided TLS mode'):
                tls_secrets.provision(NAMES)
        self.assertEqual(self.created(), [])


class ProvidedModeTests(SecretTest):
    def test_a_request_keeps_its_key_in_a_secret_and_only_the_csr_leaves(self):
        tls_secrets.provision(NAMES)
        request = self.request()
        with patch('subprocess.run', REAL_RUN):
            self.assertEqual(app_ca.requested_names(request), NAMES)
        self.assertNotIn('PRIVATE KEY', request.read_text())
        key = self.secret('request.key')
        self.assertIn('PRIVATE KEY', key)
        self.request()
        self.assertEqual(self.secret('request.key'), key)
        self.request(new_key=True)
        self.assertNotEqual(self.secret('request.key'), key)

    def test_a_signed_request_switches_to_provided_mode(self):
        tls_secrets.provision(NAMES)
        request = self.request()
        key = self.secret('request.key')
        certificate = self.sign(request)
        self.assertTrue(self.install(certificate))
        self.assertEqual(self.secret('tls-mode'), 'provided\n')
        self.assertEqual(self.secret('server.key'), key)
        self.assertEqual(self.secret('server.crt'), certificate.read_text())
        self.assertEqual(self.secret('ca.crt'), (self.ca / 'ca.crt').read_text())
        # The demo CA's key, the waiting key and the incoming certificates are gone.
        for name in ('ca.key', 'request.key', 'incoming.crt', 'incoming-ca.crt'):
            self.assertIsNone(self.secret(name), name)
        self.assertEqual(kube_files(self.host), {'tls-mode': 'provided\n', 'ca.crt': (self.ca / 'ca.crt').read_text(),
                                                 'server.crt': certificate.read_text(), 'server.key': key})
        self.assertFalse(self.install(certificate))
        # From now on an install issues nothing: provision only publishes.
        self.host.calls.clear()
        self.assertFalse(tls_secrets.provision(NAMES))
        self.assertFalse([argv for argv in self.host.ran('podman', 'run') if 'genpkey' in argv])

    def test_a_certificate_that_does_not_fit_changes_nothing(self):
        tls_secrets.provision(NAMES)
        request = self.request()
        other = self.directory / 'other.csr'
        openssl('req', '-new', '-newkey', 'rsa:2048', '-noenc', '-keyout', 'o.key', '-subj', f'/CN={NAMES[0]}',
                '-addext', 'subjectAltName=' + ','.join(f'DNS:{n}' for n in NAMES), '-out', other,
                cwd=self.directory)
        second = self.directory / 'second-ca'
        with patch('subprocess.run', REAL_RUN):
            app_ca.init(second, ['example.test'], 'Another CA', passphrase_file=self.passphrase)
        certificate = self.sign(request)
        for message, arguments in (
                ('neither the waiting request', (self.sign(other),)),
                ('not valid for extra.example.test', (certificate, None, NAMES + ['extra.example.test'])),
                ('not valid for todo.example.test', (self.sign(request, ca=second),)),
                ('not a self-signed root', (certificate, certificate))):
            with self.subTest(message):
                before = dict(self.host.secrets)
                with self.assertRaisesRegex(tls_secrets.TlsError, message):
                    self.install(*arguments)
                self.assertEqual(self.host.secrets, before)

    def test_a_private_key_is_never_taken_as_a_certificate_file(self):
        tls_secrets.provision(NAMES)
        bundle = self.directory / 'with-key.pem'
        bundle.write_text(self.sign(self.request()).read_text() + self.secret('request.key'))
        with self.assertRaisesRegex(tls_secrets.TlsError, 'never a private key'):
            self.install(bundle)

    def test_a_renewal_keeps_the_active_pair_until_the_new_one_passed(self):
        tls_secrets.provision(NAMES)
        self.install(self.sign(self.request()))
        active = kube_files(self.host)
        renewal = self.request(new_key=True)
        self.assertEqual(kube_files(self.host), active)
        pending = self.secret('request.key')
        second = self.sign(renewal)
        self.assertTrue(self.install(second))
        self.assertEqual(kube_files(self.host)['server.key'], pending)
        self.assertEqual(kube_files(self.host)['server.crt'], second.read_text())
        # The same key renewed again is accepted too.
        third = self.sign(renewal, days=400)
        self.assertTrue(self.install(third))
        self.assertEqual(kube_files(self.host)['server.key'], pending)

    def test_provided_mode_without_its_files_stops_before_nginx_starts(self):
        tls_secrets.provision(NAMES)
        self.install(self.sign(self.request()))
        self.host.secrets.pop(SECRET['server.key'])
        with self.assertRaisesRegex(tls_secrets.TlsError, 'server.key is missing; run tls-request'):
            tls_secrets.provision(NAMES)


class RestartTests(SecretTest):
    def setUp(self):
        super().setUp(nginx='systemd')

    def test_a_running_nginx_restarts_and_must_serve_the_new_certificate(self):
        tls_secrets.provision(NAMES)
        certificate = self.sign(self.request())
        self.install(certificate)
        self.assertIn(['systemctl', '--user', 'restart', 'shared-proxy.service'], self.host.calls)
        self.assertEqual(self.host.serving, certificate.read_text())
        # An nginx that still serves the old certificate after its restart is an error.
        with patch.object(self.host, 'answer', side_effect=lambda argv, input: (0, 'SHA256 Fingerprint=OLD')
                          if argv[:2] == ['podman', 'exec'] else SecretHost.answer(self.host, argv, input)), \
                patch.object(tls_secrets.time, 'sleep'), \
                self.assertRaisesRegex(tls_secrets.TlsError, 'does not serve the new certificate'):
            self.install(self.sign(self.request(), days=300))

    def test_renew_restarts_nginx_only_when_its_certificate_changed(self):
        tls_secrets.provision(NAMES)
        self.host.calls.clear()
        self.assertFalse(tls_secrets.renew())
        self.assertNotIn(['systemctl', '--user', 'restart', 'shared-proxy.service'], self.host.calls)
        self.host.secrets.pop(SECRET['server.crt'])
        self.assertTrue(tls_secrets.renew())
        self.assertIn(['systemctl', '--user', 'restart', 'shared-proxy.service'], self.host.calls)

    def test_a_development_nginx_must_be_started_again_by_hand(self):
        self.host.nginx = 'development'
        tls_secrets.provision(NAMES)
        with self.assertRaisesRegex(tls_secrets.TlsError, 'dev-up.sh'):
            self.install(self.sign(self.request()))
        self.assertEqual(self.secret('tls-mode'), 'provided\n')


class CheckTests(SecretTest):
    def test_status_runs_the_entrypoints_check_on_the_secrets(self):
        self.assertEqual(tls_secrets.status(NAMES), ('local', None, ''))
        tls_secrets.provision(NAMES)
        current, days, problem = tls_secrets.status(NAMES)
        self.assertEqual((current, problem), ('local', ''))
        self.assertIn(days, (396, 397))
        self.install(self.sign(self.request()))
        self.assertEqual(tls_secrets.status(NAMES)[0], 'provided')
        self.host.secrets.pop(SECRET['server.key'])
        self.assertEqual(tls_secrets.status(NAMES)[2],
                         'provided TLS mode, but server.key is missing; run app_installer tls-install')

    def test_thirty_days_before_the_end_it_is_a_problem_with_the_way_out(self):
        with patch.object(tls_secrets, 'LEAF_DAYS', 20):
            tls_secrets.provision(NAMES)
        lines, problems = tls_secrets.check()
        self.assertRegex(lines[0], r'nginx certificate \(local mode, Podman secrets\): valid 1[89] more days')
        self.assertIn('tls-renew renews it', problems[0])
        self.install(self.sign(self.request(), days=20))
        self.assertIn('run tls-request', tls_secrets.check()[1][0])

    def test_a_host_without_a_certificate_reports_nothing(self):
        self.assertEqual(tls_secrets.check(), ([], []))


class CommandTests(SecretTest):
    def test_request_install_status_and_renew_from_the_command_line(self):
        tls_secrets.provision(NAMES)
        output = self.directory / 'cli.csr'
        with patch('sys.stdout') as stdout, patch('sys.stderr'):
            self.assertEqual(cli.main(['tls-request', '--output', str(output)]), 0)
        self.assertEqual(json.loads(stdout.write.call_args_list[0].args[0])['hostnames'], NAMES)
        with patch('sys.stdout') as stdout:
            self.assertEqual(cli.main(['tls-install', '--certificate', str(self.sign(output)),
                                       '--ca', str(self.ca / 'ca.crt')]), 0)
        self.assertEqual(json.loads(stdout.write.call_args_list[0].args[0]), {'changed': True})
        with patch('sys.stdout') as stdout:
            self.assertEqual(cli.main(['tls-status']), 0)
        self.assertIn('provided mode, Podman secrets', ''.join(c.args[0] for c in stdout.write.call_args_list))
        with patch('sys.stdout') as stdout:
            self.assertEqual(cli.main(['tls-renew']), 0)
        self.assertEqual(json.loads(stdout.write.call_args_list[0].args[0]), {'changed': False})

    def test_tls_renew_is_only_for_podman_secrets(self):
        with patch.object(settings, 'NGINX_TLS_STORAGE', 'volume'), patch('sys.stderr') as stderr:
            self.assertEqual(cli.main(['tls-renew']), 1)
        self.assertIn('restart shared-proxy.service', ''.join(c.args[0] for c in stderr.write.call_args_list))


class InstallTests(SecretTest):
    def test_uninstall_with_its_data_removes_every_tls_secret(self):
        from app_installer import uninstall
        for name in tls_secrets.secret_names():
            self.assertIn(name, uninstall.SECRETS)


if __name__ == '__main__':
    unittest.main()
