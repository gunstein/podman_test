"""tls.py: nginx's certificate in the TLS volume, with real openssl and a Podman that runs it here.

These tests keep the volume storage working for going back to it: each
sets settings.NGINX_TLS_STORAGE to "volume". Podman secrets, the default,
are test_tls_secrets.py.

FakePodman runs what tls.py starts in a throwaway proxy container (podman
run ... /bin/sh -c ...) as a local process in a temporary directory that
plays the TLS volume, so every openssl step is real. Certificates come from
the CA tool, deploy/scripts/app_ca.py, run on this same host from its own
directory, apart from the directory that plays the TLS volume. The image's
entrypoint is this repository's proxy/proxy-entrypoint.sh, and the host's
recorded hostnames are a file of the test's own.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, cli, tls, tls_store  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('app_ca', ROOT / 'deploy/scripts/app_ca.py')
assert SPEC and SPEC.loader
app_ca = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_ca)
REAL_RUN = subprocess.run
NAMES = ['todo.example.test', 'notes.example.test']


def completed(argv, returncode=0, stdout=''):
    return subprocess.CompletedProcess(argv, returncode, stdout, '')


class FakePodman:
    """The TLS volume is a directory; the proxy image's commands run here; nginx is a recording."""

    def __init__(self, test, volume, label='local provided', running=False):
        self.volume, self.label, self.running = Path(volume), label, running
        self.volume_exists = True
        self.calls, self.served = [], None
        patcher = patch('app_installer.commands.subprocess.run', side_effect=self)
        patcher.start()
        test.addCleanup(patcher.stop)

    def __call__(self, argv, input=None, timeout=None, **kwargs):
        argv = [str(part) for part in argv]
        if argv[0] != 'podman':  # subprocess.run is one function: app_ca's openssl comes here too
            return REAL_RUN(argv, input=input, timeout=timeout, **kwargs)
        self.calls.append(argv)
        if argv[:2] == ['podman', 'run']:
            self.assert_throwaway(argv)
            script = argv[argv.index('--entrypoint') + 3:]
            return REAL_RUN(['sh', *script], cwd=self.volume, input=input, text=True, capture_output=True,
                            check=False)
        if argv[:3] == ['podman', 'volume', 'exists']:
            return completed(argv, 0 if self.volume_exists and argv[3] == apps.NGINX_TLS_VOLUME else 1)
        if argv[:3] == ['podman', 'image', 'inspect']:
            return completed(argv, stdout=json.dumps([{'Labels': {tls.MODES_LABEL: self.label}}]))
        if argv[:3] == ['podman', 'container', 'exists']:
            return completed(argv, 0 if self.running else 1)
        if argv[:3] == ['podman', 'inspect', '--format']:
            return completed(argv, stdout='true\n')
        if argv[:2] == ['podman', 'exec'] and 'reload' in argv:
            self.served = self.fingerprint() if self.served is None else self.served
            return completed(argv)
        if argv[:2] == ['podman', 'exec'] and argv[-2] == 'served':
            return completed(argv, stdout=(self.served or '') + '\n')
        raise AssertionError(f'Unexpected command: {argv}')

    def assert_throwaway(self, argv):
        """Every TLS step runs without network, as the nginx user, on the TLS volume only."""
        assert argv[argv.index('--network') + 1] == 'none', argv
        assert argv[argv.index('--user') + 1] == '101:101', argv
        assert argv[argv.index('--volume') + 1] == f'{apps.NGINX_TLS_VOLUME}:{tls.DIRECTORY}', argv
        assert argv[argv.index('--entrypoint') + 2] == apps.PROXY_IMAGE, argv

    def fingerprint(self):
        return REAL_RUN(['openssl', 'x509', '-in', 'server.crt', '-noout', '-fingerprint', '-sha256'],
                        cwd=self.volume, text=True, capture_output=True, check=True).stdout.strip()

    def file(self, name):
        path = self.volume / name
        return path.read_text() if path.exists() else None


class TlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One CA for the class: a new RSA key per test would only make the run slower.
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
        self.record = self.directory / 'target-values.json'
        self.record.write_text(json.dumps({'TARGET_EXTERNAL_HOSTNAME': NAMES[0], 'TARGET_NOTES_HOSTNAME': NAMES[1]}))
        self.podman = FakePodman(self, self.volume)
        for target, name, value in ((tls, 'KEY', 'rsa:2048'),  # a smaller key only to keep the tests fast
                                    (tls, 'ENTRYPOINT', str(ROOT / 'proxy/proxy-entrypoint.sh')),
                                    (tls.target_render, 'record_path', lambda: self.record),
                                    (tls_store.settings, 'NGINX_TLS_STORAGE', 'volume')):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, new_key=False):
        output = self.directory / 'host.csr'
        names = tls.request(output, new_key)
        return output, names

    def sign(self, request, days=365, ca=None):
        output = self.directory / f'signed-{len(list(self.directory.glob("signed-*")))}.crt'
        app_ca.sign(ca or self.ca, request, output, days, passphrase_file=self.passphrase)
        return output

    def install(self, certificate, ca=None, hostnames=None):
        return tls.install(certificate, ca or self.ca / 'ca.crt', hostnames)

    def local_mode(self):
        """What the demo CA leaves in the volume: its own CA key and certificate, and a leaf."""
        for name in ('ca.key', 'ca.srl', 'server.key', 'server.crt', 'ca.crt'):
            (self.volume / name).write_text(f'demo {name}\n')


class RequestTests(TlsTest):
    def test_a_request_names_every_public_hostname_and_keeps_its_key_in_the_volume(self):
        output, names = self.request()
        self.assertEqual(names, NAMES)
        text = output.read_text()
        self.assertIn('BEGIN CERTIFICATE REQUEST', text)
        self.assertNotIn('PRIVATE KEY', text)
        self.assertEqual(app_ca.requested_names(output), NAMES)
        self.assertEqual(oct((self.volume / tls.REQUEST_KEY).stat().st_mode & 0o777), '0o600')

    def test_a_second_request_keeps_the_waiting_key_unless_a_new_one_is_asked_for(self):
        self.request()
        key = self.podman.file(tls.REQUEST_KEY)
        self.request()
        self.assertEqual(self.podman.file(tls.REQUEST_KEY), key)
        self.request(new_key=True)
        self.assertNotEqual(self.podman.file(tls.REQUEST_KEY), key)

    def test_a_host_without_the_volume_or_with_an_old_proxy_image_is_refused(self):
        self.podman.volume_exists = False
        with self.assertRaisesRegex(tls.TlsError, 'install the stack first'):
            self.request()
        self.podman.volume_exists, self.podman.label = True, ''
        with self.assertRaisesRegex(tls.TlsError, 'does not know provided TLS mode'):
            self.request()
        self.assertFalse((self.volume / tls.REQUEST_KEY).exists())


class InstallTests(TlsTest):
    def test_a_signed_request_switches_the_volume_to_provided_mode(self):
        self.local_mode()
        request, _ = self.request()
        key = self.podman.file(tls.REQUEST_KEY)
        certificate = self.sign(request)
        self.assertTrue(self.install(certificate))
        self.assertEqual(self.podman.file(tls.MODE_FILE), 'provided\n')
        self.assertEqual(self.podman.file('server.key'), key)
        self.assertEqual(self.podman.file('server.crt'), certificate.read_text())
        self.assertEqual(self.podman.file('ca.crt'), (self.ca / 'ca.crt').read_text())
        # The demo CA and the request are gone; nothing half-installed is left.
        self.assertEqual(sorted(path.name for path in self.volume.iterdir()),
                         ['ca.crt', 'server.crt', 'server.key', 'tls-mode'])
        self.assertEqual(oct((self.volume / 'server.key').stat().st_mode & 0o777), '0o600')
        # The certificates are public, as in local mode: podman cp hands ca.crt to clients.
        self.assertEqual(oct((self.volume / 'ca.crt').stat().st_mode & 0o777), '0o644')
        # Installing the same files again changes nothing.
        self.assertFalse(self.install(certificate))

    def test_the_whole_lifecycle_runs_with_the_ca_on_the_same_host_and_keeps_both_keys_apart(self):
        # The CA directory and the TLS volume are two directories of this one host.
        self.local_mode()
        request, _ = self.request()
        server_key = self.podman.file(tls.REQUEST_KEY)
        self.assertTrue(self.install(self.sign(request)))
        ca_key = (self.ca / 'ca.key').read_text()
        volume = {path.name: path.read_text() for path in self.volume.iterdir()}
        # nginx's key stayed in the volume; the CA key never went there, nor did any encrypted key.
        self.assertEqual(volume['server.key'], server_key)
        self.assertNotIn(ca_key, volume.values())
        self.assertFalse(any('ENCRYPTED PRIVATE KEY' in text for text in volume.values()))
        self.assertFalse((self.volume / 'ca.key').exists())
        # And the CA directory never received nginx's key: only its own state.
        self.assertEqual(sorted(path.name for path in self.ca.iterdir()), ['ca.crt', 'ca.key', 'issued.log'])
        self.assertFalse(any(server_key in path.read_text() for path in self.ca.iterdir()))
        # Outside the volume only the public request exists.
        self.assertNotIn('PRIVATE KEY', request.read_text())

    def test_a_renewal_keeps_the_active_key_and_certificate_until_the_new_ones_are_valid(self):
        request, _ = self.request()
        first = self.sign(request)
        self.install(first)
        active = (self.podman.file('server.key'), self.podman.file('server.crt'))
        # 1-2: a new key and request wait beside the active pair, which nginx keeps serving.
        renewal, _ = self.request(new_key=True)
        pending = self.podman.file(tls.REQUEST_KEY)
        self.assertNotEqual(pending, active[0])
        self.assertEqual((self.podman.file('server.key'), self.podman.file('server.crt')), active)
        # A certificate that does not fit the pending or the active key changes nothing.
        stranger = self.directory / 'stranger.csr'
        REAL_RUN(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-noenc', '-keyout', str(self.directory / 's.key'),
                  '-subj', '/CN=todo.example.test',
                  '-addext', 'subjectAltName=DNS:todo.example.test,DNS:notes.example.test', '-out', str(stranger)],
                 capture_output=True, check=True)
        with self.assertRaisesRegex(tls.TlsError, 'neither the waiting request'):
            self.install(self.sign(stranger))
        self.assertEqual((self.podman.file('server.key'), self.podman.file('server.crt')), active)
        self.assertEqual(self.podman.file(tls.REQUEST_KEY), pending)
        # 3-5: the signed renewal is checked, then the pending key becomes the active one.
        second = self.sign(renewal)
        self.assertTrue(self.install(second))
        self.assertEqual(self.podman.file('server.key'), pending)
        self.assertEqual(self.podman.file('server.crt'), second.read_text())
        self.assertFalse((self.volume / tls.REQUEST_KEY).exists())

    def test_a_renewal_for_the_same_key_is_accepted(self):
        request, _ = self.request()
        self.install(self.sign(request))
        key = self.podman.file('server.key')
        renewal = self.sign(request, days=400)
        self.assertTrue(self.install(renewal))
        self.assertEqual(self.podman.file('server.key'), key)
        self.assertEqual(self.podman.file('server.crt'), renewal.read_text())

    def assert_refused(self, message, certificate, ca=None, hostnames=None):
        before = {path.name: path.read_bytes() for path in self.volume.iterdir()}
        with self.assertRaisesRegex(tls.TlsError, message):
            self.install(certificate, ca, hostnames)
        self.assertEqual({path.name: path.read_bytes() for path in self.volume.iterdir()}, before)

    def test_a_certificate_that_does_not_fit_changes_nothing(self):
        self.local_mode()
        request, _ = self.request()
        # Another host's request: the same names, but not the key that waits here.
        other = self.directory / 'other.csr'
        REAL_RUN(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-noenc', '-keyout', str(self.directory / 'o.key'),
                  '-subj', '/CN=todo.example.test',
                  '-addext', 'subjectAltName=DNS:todo.example.test,DNS:notes.example.test', '-out', str(other)],
                 capture_output=True, check=True)
        self.assert_refused('neither the waiting request', self.sign(other))
        # A certificate that misses a hostname nginx serves.
        self.assert_refused('not valid for extra.example.test', self.sign(request),
                            hostnames=NAMES + ['extra.example.test'])
        # A certificate from another CA, and a CA file that is not a root.
        second = self.directory / 'second-ca'
        app_ca.init(second, ['example.test'], 'Another CA', passphrase_file=self.passphrase)
        self.assert_refused('not valid for todo.example.test', self.sign(request, ca=second))
        certificate = self.sign(request)
        self.assert_refused('not a self-signed root', certificate, ca=certificate)

    def test_a_private_key_is_never_taken_as_a_certificate_file(self):
        request, _ = self.request()
        certificate = self.sign(request)
        bundle = self.directory / 'with-key.pem'
        bundle.write_text(certificate.read_text() + (self.ca / 'ca.key').read_text())
        with self.assertRaisesRegex(tls.TlsError, 'never a private key'):
            self.install(bundle)

    def test_a_running_nginx_reloads_and_must_serve_the_new_certificate(self):
        self.podman.running = True
        request, _ = self.request()
        self.install(self.sign(request))
        self.assertIn(['podman', 'exec', 'nginx', 'nginx', '-c', '/etc/todo-nginx/nginx.conf', '-s', 'reload'],
                      self.podman.calls)
        # nginx that still serves an old certificate after the reload is an error.
        self.podman.served = 'SHA256 Fingerprint=OLD'
        with patch.object(tls.time, 'sleep'), \
                self.assertRaisesRegex(tls.TlsError, 'does not serve the new certificate for todo.example.test'):
            self.install(self.sign(request, days=300))


class CheckTests(TlsTest):
    def provided(self, days):
        request, _ = self.request()
        self.install(self.sign(request, days=days))

    def test_a_host_without_a_certificate_reports_nothing(self):
        self.assertEqual(tls.check(), ([], []))
        self.podman.volume_exists = False
        self.assertEqual(tls.check(), ([], []))

    def test_a_certificate_far_from_its_end_is_fine(self):
        self.provided(365)
        lines, problems = tls.check()
        self.assertEqual(problems, [])
        self.assertIn(lines, (['nginx certificate (provided mode): valid 364 more days'],
                              ['nginx certificate (provided mode): valid 365 more days']))

    def test_thirty_days_before_the_end_it_is_a_problem_and_nothing_is_prepared(self):
        self.provided(20)
        before = sorted(path.name for path in self.volume.iterdir())
        _lines, problems = tls.check()
        self.assertEqual(len(problems), 1)
        self.assertRegex(problems[0], r'the nginx certificate expires in 1[89] days; run tls-request')
        # The nightly look only reports: the CA step makes the next request.
        self.assertEqual(sorted(path.name for path in self.volume.iterdir()), before)

    def test_a_certificate_that_would_stop_nginx_is_a_problem(self):
        self.provided(365)
        (self.volume / 'server.key').unlink()
        self.assertEqual(tls.check(), ([], ['the nginx certificate would stop nginx at its next start: '
                                            'provided TLS mode, but server.key is missing; '
                                            'run app_installer tls-install']))

    def test_a_local_demo_certificate_near_its_end_asks_for_a_restart(self):
        REAL_RUN(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '10',
                  '-keyout', 'server.key', '-out', 'server.crt', '-subj', '/CN=todo.example.test',
                  '-addext', 'subjectAltName=DNS:todo.example.test,DNS:notes.example.test'],
                 cwd=self.volume, capture_output=True, check=True)
        REAL_RUN(['cp', 'server.crt', 'ca.crt'], cwd=self.volume, check=True)
        lines, problems = tls.check()
        self.assertRegex(lines[0], r'nginx certificate \(local mode\): valid [89] more days')
        self.assertIn('restart shared-proxy.service', problems[0])

    def test_without_recorded_hostnames_it_cannot_look(self):
        self.provided(365)
        self.record.unlink()
        with self.assertRaisesRegex(tls.TlsError, 'records no public hostnames'):
            tls.check()


class StatusTests(TlsTest):
    def test_status_is_the_entrypoints_check_with_the_days_left(self):
        self.assertEqual(tls.status(NAMES), ('local', None, ''))
        request, _ = self.request()
        self.install(self.sign(request))
        current, days, problem = tls.status(NAMES)
        self.assertEqual((current, problem), ('provided', ''))
        self.assertIn(days, (364, 365))
        self.assertEqual(tls.status(NAMES + ['extra.example.test'])[2],
                         'provided TLS mode, but server.crt is not valid for extra.example.test from ca.crt')
        (self.volume / 'server.key').unlink()
        self.assertEqual(tls.status(NAMES),
                         ('provided', None, 'provided TLS mode, but server.key is missing; run app_installer tls-install'))
        (self.volume / tls.MODE_FILE).write_text('custom\n')
        self.assertEqual(tls.status(NAMES)[1:], (None, 'unknown TLS mode in ./tls-mode: custom'))

    def test_the_recorded_hostnames_put_the_shared_one_first(self):
        self.record.write_text(json.dumps({'TARGET_NOTES_HOSTNAME': 'notes.example.test',
                                           'TARGET_EXTERNAL_HOSTNAME': 'todo.example.test'}))
        self.assertEqual(tls.recorded_hostnames(), NAMES)
        self.record.unlink()
        with self.assertRaisesRegex(tls.TlsError, 'records no public hostnames'):
            tls.recorded_hostnames()


class CommandTests(TlsTest):
    def test_request_install_and_status_from_the_command_line(self):
        output = self.directory / 'cli.csr'
        with patch('sys.stdout') as stdout, patch('sys.stderr'):
            self.assertEqual(cli.main(['tls-request', '--output', str(output)]), 0)
        result = json.loads(stdout.write.call_args_list[0].args[0])
        self.assertEqual(result['hostnames'], NAMES)
        certificate = self.sign(output)
        with patch('sys.stdout') as stdout:
            self.assertEqual(cli.main(['tls-install', '--certificate', str(certificate),
                                       '--ca', str(self.ca / 'ca.crt')]), 0)
        self.assertEqual(json.loads(stdout.write.call_args_list[0].args[0]), {'changed': True})
        with patch('sys.stdout'):
            self.assertEqual(cli.main(['tls-status']), 0)

    def test_a_refused_certificate_is_one_error_line_and_exit_1(self):
        self.request()
        stranger = self.directory / 'stranger.crt'
        stranger.write_text((self.ca / 'ca.crt').read_text())
        with patch('sys.stderr') as stderr:
            self.assertEqual(cli.main(['tls-install', '--certificate', str(stranger), '--ca', str(self.ca / 'ca.crt')]), 1)
        self.assertIn('app-installer: The certificate is not valid for todo.example.test',
                      ''.join(call.args[0] for call in stderr.write.call_args_list))

    def test_the_nightly_backup_also_reports_the_certificate(self):
        with patch.object(cli.backup, 'nightly', return_value=(['todo: verified base backup x'], [])), \
                patch.object(tls, 'check', return_value=(['nginx certificate (provided mode): valid 9 more days'],
                                                         ['the nginx certificate expires in 9 days'])), \
                patch('sys.stdout') as stdout, patch('sys.stderr') as stderr:
            self.assertEqual(cli.main(['backup', 'nightly', '--keep-days', '7']), 1)
        self.assertIn('valid 9 more days', ''.join(call.args[0] for call in stdout.write.call_args_list))
        self.assertIn('ERROR: the nginx certificate expires in 9 days',
                      ''.join(call.args[0] for call in stderr.write.call_args_list))
        # A failure to look is a problem of its own, and the backup lines still print.
        with patch.object(cli.backup, 'nightly', return_value=(['todo: verified base backup x'], [])), \
                patch.object(tls, 'check', side_effect=RuntimeError('podman run failed')), \
                patch('sys.stdout'), patch('sys.stderr') as stderr:
            self.assertEqual(cli.main(['backup', 'nightly', '--keep-days', '7']), 1)
        self.assertIn('cannot check the nginx certificate: podman run failed',
                      ''.join(call.args[0] for call in stderr.write.call_args_list))


if __name__ == '__main__':
    unittest.main()
