"""deploy/scripts/app_ca.py and platform-ca-sign: the CA for nginx's certificates, with real openssl.

The CA runs on the same host as Podman here, as in development; the tests
check what keeps it apart from nginx: its own storage, its own permissions,
and a signing step that takes only a CSR.
"""
import importlib.util
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('app_ca', ROOT / 'deploy/scripts/app_ca.py')
assert SPEC and SPEC.loader
app_ca = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_ca)


def openssl(*arguments, check=True, cwd=None):
    return subprocess.run(['openssl', *map(str, arguments)], capture_output=True, text=True, check=check, cwd=cwd)


class CaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(tempfile.mkdtemp())
        cls.addClassCleanup(lambda: subprocess.run(['rm', '-rf', str(cls.directory)], check=True))
        cls.passphrase = cls.directory / 'passphrase'
        cls.passphrase.write_text('a test passphrase\n')
        cls.ca = cls.directory / 'ca'
        app_ca.init(cls.ca, ['example.test', 'Second.Test.'], 'Test root CA', passphrase_file=cls.passphrase)

    def request(self, name, *san):
        path = self.directory / f'{name}.csr'
        openssl('req', '-new', '-newkey', 'rsa:2048', '-noenc', '-keyout', self.directory / f'{name}.key',
                '-subj', f'/CN={name}', *(['-addext', 'subjectAltName=' + ','.join(san)] if san else []),
                '-out', path)
        return path

    def sign(self, request, days=app_ca.SERVER_DAYS):
        output = request.with_suffix('.crt')
        return app_ca.sign(self.ca, request, output, days, passphrase_file=self.passphrase), output

    def test_the_ca_is_an_encrypted_root_limited_to_its_domains(self):
        self.assertIn('ENCRYPTED PRIVATE KEY', (self.ca / 'ca.key').read_text())
        self.assertEqual(oct((self.ca / 'ca.key').stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct(self.ca.stat().st_mode & 0o777), '0o700')
        text = openssl('x509', '-in', self.ca / 'ca.crt', '-noout', '-text').stdout
        self.assertIn('CA:TRUE, pathlen:0', text)
        self.assertEqual(app_ca.permitted_domains(self.ca / 'ca.crt'), ['example.test', 'second.test'])
        # Without the passphrase the key is useless.
        self.assertNotEqual(openssl('pkey', '-in', self.ca / 'ca.key', '-passin', 'pass:wrong', check=False)
                            .returncode, 0)

    def test_init_leaves_exactly_the_ca_state_with_private_permissions(self):
        self.assertEqual(sorted(path.name for path in self.ca.iterdir()), ['ca.crt', 'ca.key', 'issued.log'])
        modes = {path.name: oct(path.stat().st_mode & 0o777) for path in self.ca.iterdir()}
        self.assertEqual(modes, {'ca.crt': '0o644', 'ca.key': '0o600', 'issued.log': '0o600'})
        self.assertNotIn('BEGIN PRIVATE KEY', (self.ca / 'ca.key').read_text())
        # The passphrase is not stored with the key.
        self.assertNotIn('a test passphrase', ''.join(path.read_text() for path in self.ca.iterdir()))

    def test_a_passphrase_file_next_to_the_key_is_refused(self):
        inside = self.directory / 'inside-ca'
        inside.mkdir(mode=0o700)
        (inside / 'passphrase').write_text('next to the key\n')
        with self.assertRaisesRegex(app_ca.CaError, 'Keep the passphrase file out of'):
            app_ca.init(inside, ['example.test'], 'Inside', passphrase_file=inside / 'passphrase')
        self.assertFalse((inside / 'ca.key').exists())
        request = self.request('beside', 'DNS:todo.example.test')
        (self.ca / 'passphrase').write_text('x\n')
        self.addCleanup((self.ca / 'passphrase').unlink)
        with self.assertRaisesRegex(app_ca.CaError, 'Keep the passphrase file out of'):
            app_ca.sign(self.ca, request, self.directory / 'beside.crt', passphrase_file=self.ca / 'passphrase')

    def test_a_ca_others_can_read_is_never_used(self):
        request = self.request('open', 'DNS:todo.example.test')
        for path, mode in ((self.ca, 0o755), (self.ca / 'ca.key', 0o640)):
            with self.subTest(path=path.name):
                before = path.stat().st_mode & 0o777
                os.chmod(path, mode)
                try:
                    with self.assertRaisesRegex(app_ca.CaError, 'must be 0700 and ca.key 0600'):
                        self.sign(request)
                finally:
                    os.chmod(path, before)
        self.assertFalse((self.directory / 'open.crt').exists())

    def test_an_invalid_request_is_refused(self):
        garbage = self.directory / 'garbage.csr'
        garbage.write_text('-----BEGIN CERTIFICATE REQUEST-----\nbm90IGEgcmVxdWVzdA==\n-----END CERTIFICATE REQUEST-----\n')
        tampered = self.directory / 'tampered.csr'
        text = self.request('tampered-source', 'DNS:todo.example.test').read_text().splitlines()
        # Change one character inside the signed body: the self-signature no longer verifies.
        body = text[3]
        text[3] = body[:10] + ('A' if body[10] != 'A' else 'B') + body[11:]
        tampered.write_text('\n'.join(text) + '\n')
        weak = self.directory / 'weak.csr'
        openssl('req', '-new', '-newkey', 'rsa:1024', '-noenc', '-keyout', self.directory / 'weak.key',
                '-subj', '/CN=weak', '-addext', 'subjectAltName=DNS:todo.example.test', '-out', weak)
        for request, message in ((garbage, 'signature verifies'), (tampered, 'signature verifies'),
                                 (weak, 'key this CA does not accept'),
                                 (self.request('wild', 'DNS:*.example.test'), 'not plain DNS names')):
            with self.subTest(request=request.name), self.assertRaisesRegex(app_ca.CaError, message):
                self.sign(request)

    def test_nothing_but_the_key_and_names_comes_from_the_request(self):
        request = self.directory / 'greedy.csr'
        openssl('req', '-new', '-newkey', 'rsa:2048', '-noenc', '-keyout', self.directory / 'greedy.key',
                '-subj', '/CN=evil/O=Some Bank', '-addext', 'subjectAltName=DNS:todo.example.test',
                '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign',
                '-out', request)
        _names, certificate = self.sign(request)
        text = openssl('x509', '-in', certificate, '-noout', '-text').stdout
        self.assertIn('Subject: CN = todo.example.test', text)
        self.assertNotIn('Some Bank', text)
        self.assertIn('CA:FALSE', text)
        self.assertNotIn('Certificate Sign', text)

    def test_sign_stdin_takes_a_request_and_gives_only_a_certificate(self):
        request = self.request('stdin', 'DNS:todo.example.test')
        result = subprocess.run(['python3', ROOT / 'deploy/scripts/app_ca.py', 'sign-stdin', '--directory',
                                 str(self.ca), '--passphrase-file', str(self.passphrase)],
                                input=request.read_text(), capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('-----BEGIN CERTIFICATE-----'))
        self.assertNotIn('PRIVATE KEY', result.stdout)
        with self.assertRaisesRegex(app_ca.CaError, 'one PEM certificate signing request'):
            app_ca.sign_request(self.ca, 'not a request', self.passphrase)
        with self.assertRaisesRegex(app_ca.CaError, 'one PEM certificate signing request'):
            app_ca.sign_request(self.ca, request.read_text() + 'x' * app_ca.MAX_REQUEST_BYTES, self.passphrase)
        log = (self.ca / 'issued.log').read_text()
        self.assertIn('names=todo.example.test', log)
        self.assertIn(f'by_uid={os.getuid()}', log)
        self.assertEqual(oct((self.ca / 'issued.log').stat().st_mode & 0o777), '0o600')

    def test_the_sudo_wrapper_takes_no_arguments_and_fixes_every_path(self):
        wrapper = ROOT / 'deploy/scripts/platform-ca-sign'
        result = subprocess.run(['sh', wrapper, '--directory', '/tmp/elsewhere'], capture_output=True, text=True,
                                check=False, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 2)
        self.assertIn('takes no arguments', result.stderr)
        text = wrapper.read_text()
        self.assertNotIn('"$@"', text)
        self.assertNotIn('$*', text)
        self.assertIn('--directory /var/lib/platform-ca', text)
        self.assertIn('/usr/bin/python3 -I /usr/local/lib/platform-ca/app_ca.py sign-stdin', text)
        self.assertEqual(str(app_ca.V1_DIRECTORY), '/var/lib/platform-ca')

    def test_a_second_ca_in_the_same_directory_and_a_bad_domain_are_refused(self):
        with self.assertRaisesRegex(app_ca.CaError, 'already holds a CA'):
            app_ca.init(self.ca, ['example.test'], 'Again', passphrase_file=self.passphrase)
        with self.assertRaisesRegex(app_ca.CaError, 'Not a DNS domain'):
            app_ca.init(self.directory / 'bad', ['example test'], 'Bad', passphrase_file=self.passphrase)

    def test_a_host_request_becomes_a_server_certificate_for_its_names(self):
        names, certificate = self.sign(self.request('host', 'DNS:todo.example.test', 'DNS:app.second.test'))
        self.assertEqual(names, ['todo.example.test', 'app.second.test'])
        for name in names:
            self.assertEqual(openssl('verify', '-CAfile', self.ca / 'ca.crt', '-purpose', 'sslserver',
                                     '-verify_hostname', name, certificate).returncode, 0)
        text = openssl('x509', '-in', certificate, '-noout', '-text').stdout
        self.assertIn('CA:FALSE', text)
        self.assertIn('TLS Web Server Authentication', text)
        self.assertIn('names=todo.example.test,app.second.test', (self.ca / 'issued.log').read_text())

    def test_names_outside_the_domains_or_of_another_kind_are_refused(self):
        cases = ((('DNS:todo.example.test', 'DNS:bank.example'), 'Outside this CA'),
                 (('DNS:example.test.evil',), 'Outside this CA'),
                 (('DNS:todo.example.test', 'IP:192.0.2.1'), 'does not issue: IP Address'),
                 ((), 'asks for no names'))
        for index, (san, message) in enumerate(cases):
            with self.subTest(san=san), self.assertRaisesRegex(app_ca.CaError, message):
                self.sign(self.request(f'refused{index}', *san))

    def test_the_name_constraints_hold_even_for_a_certificate_signed_around_the_tool(self):
        request = self.request('around', 'DNS:bank.example')
        certificate = self.directory / 'around.crt'
        extensions = self.directory / 'around.ext'
        extensions.write_text('subjectAltName=DNS:bank.example\n')
        openssl('x509', '-req', '-in', request, '-CA', self.ca / 'ca.crt', '-CAkey', self.ca / 'ca.key',
                '-passin', f'file:{self.passphrase}', '-set_serial', '7', '-days', '30',
                '-extfile', extensions, '-out', certificate)
        result = openssl('verify', '-CAfile', self.ca / 'ca.crt', certificate, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('permitted subtree violation', result.stdout + result.stderr)

    def test_longer_than_apple_clients_accept_is_refused(self):
        request = self.request('long', 'DNS:todo.example.test')
        with self.assertRaisesRegex(app_ca.CaError, 'between 1 and 825'):
            self.sign(request, days=826)
        self.sign(request, days=825)

    def test_the_command_line_prints_the_next_step_and_exit_1_on_a_refusal(self):
        request = self.request('cli', 'DNS:todo.example.test')
        output = self.directory / 'cli.crt'
        arguments = ['sign', '--directory', str(self.ca), '--request', str(request), '--output', str(output),
                     '--passphrase-file', str(self.passphrase)]
        result = subprocess.run(['python3', ROOT / 'deploy/scripts/app_ca.py', *arguments],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'tls-install --certificate {output} --ca {self.ca / "ca.crt"}', result.stdout)
        with unittest.mock.patch('sys.stderr'):
            self.assertEqual(app_ca.main(['sign', '--directory', str(self.directory / 'none'), '--request',
                                          str(request), '--output', str(output)]), 1)


if __name__ == '__main__':
    unittest.main()
