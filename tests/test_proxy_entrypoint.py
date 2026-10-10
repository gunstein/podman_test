"""proxy/proxy-entrypoint.sh in both TLS modes, run here with real openssl on a temporary volume.

PLATFORM_TLS_DIRECTORY points the script at a temporary directory instead of
/var/lib/platform-tls; the command it hands over to is `true`. The real image and
volume are covered by deploy/scripts/dev/smoke-proxy.sh in CI.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'proxy/proxy-entrypoint.sh'
NAMES = ('todo.example.test', 'notes.example.test')


def openssl(*arguments, cwd):
    subprocess.run(['openssl', *map(str, arguments)], capture_output=True, check=True, cwd=cwd)


@unittest.skipUnless(shutil.which('openssl') and os.path.exists('/etc/resolv.conf'), 'needs openssl')
class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.volume = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.volume)

    def start(self, names=NAMES, role=None):
        environment = {**os.environ, 'PLATFORM_TLS_DIRECTORY': str(self.volume),
                       'PLATFORM_TLS_HOSTNAME': names[0], 'APP_TLS_HOSTNAMES': ' '.join(names)}
        environment.pop('PLATFORM_TLS_ROLE', None)
        if role:
            environment['PLATFORM_TLS_ROLE'] = role
        return subprocess.run(['sh', SCRIPT, 'true'], env=environment, capture_output=True, text=True, check=False)

    def provided(self, names=NAMES, days=365, other_key=False):
        """What tls-install leaves: a root, a leaf it signed for names, the leaf's key, and the mode."""
        work = self.volume / 'work'
        work.mkdir()
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-keyout', 'ca.key', '-out', 'ca.crt',
                '-subj', '/CN=Test root', '-days', '30', '-addext', 'basicConstraints=critical,CA:TRUE', cwd=work)
        openssl('req', '-new', '-newkey', 'rsa:2048', '-noenc', '-keyout', 'server.key', '-out', 'server.csr',
                '-subj', f'/CN={names[0]}', cwd=work)
        (work / 'ext').write_text('subjectAltName=' + ','.join(f'DNS:{name}' for name in names) + '\n')
        openssl('x509', '-req', '-in', 'server.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-set_serial', '1',
                '-days', days, '-extfile', 'ext', '-out', 'server.crt', cwd=work)
        if other_key:
            openssl('genpkey', '-algorithm', 'RSA', '-out', 'server.key', cwd=work)
        for name in ('ca.crt', 'server.crt', 'server.key'):
            shutil.copy(work / name, self.volume / name)
        shutil.rmtree(work)
        (self.volume / 'tls-mode').write_text('provided\n')

    def test_local_mode_creates_the_demo_ca_as_before(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.volume / 'ca.key').exists())
        self.assertTrue((self.volume / 'server.crt').exists())

    def test_provided_mode_starts_with_what_it_was_given_and_issues_nothing(self):
        self.provided()
        before = {path.name: path.read_bytes() for path in self.volume.iterdir()}
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in self.volume.iterdir()}, before)
        self.assertFalse((self.volume / 'ca.key').exists())

    def test_provided_mode_never_falls_back_to_the_demo_ca(self):
        cases = (
            (lambda: (self.volume / 'server.key').unlink(), 'server.key is missing'),
            (lambda: self.provided(other_key=True), 'server.key does not belong to server.crt'),
            (lambda: None, 'not valid for extra.example.test'),
        )
        for index, (damage, message) in enumerate(cases):
            with self.subTest(message=message):
                for path in self.volume.iterdir():
                    path.unlink()
                if index != 1:
                    self.provided()
                damage()
                result = self.start(NAMES + ('extra.example.test',) if index == 2 else NAMES)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.volume / 'ca.key').exists())

    def snapshot(self):
        return {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.volume.iterdir()}

    def test_nginx_serves_a_read_only_volume_the_init_container_provisioned(self):
        # The pod: nginx-tls (provision) writes the demo certificate, then nginx (serve) only reads it.
        self.assertEqual(self.start(role='provision').returncode, 0)
        before = self.snapshot()
        os.chmod(self.volume, 0o555)
        self.addCleanup(os.chmod, self.volume, 0o700)
        result = self.start(role='serve')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_nginx_never_issues_a_certificate_itself(self):
        result = self.start(role='serve')
        self.assertEqual(result.returncode, 1)
        self.assertIn('local TLS mode, but server.crt is missing', result.stderr)
        self.assertEqual(list(self.volume.iterdir()), [])

    def test_nginx_serves_a_provided_certificate_read_only_and_checks_it(self):
        self.provided()
        before = self.snapshot()
        self.assertEqual(self.start(role='serve').returncode, 0)
        self.assertEqual(self.snapshot(), before)
        result = self.start(NAMES + ('extra.example.test',), role='serve')
        self.assertEqual(result.returncode, 1)
        self.assertIn('not valid for extra.example.test', result.stderr)

    def test_the_check_role_reports_the_mode_and_end_date_and_writes_nothing(self):
        # app_installer.tls runs this for tls-status, the nightly look and the DR readiness.
        self.assertEqual(self.start(role='check').returncode, 1)
        self.assertEqual(list(self.volume.iterdir()), [])
        self.provided()
        before = self.snapshot()
        result = self.start(role='check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r'^mode=provided\nnotAfter=.* GMT\n$')
        self.assertEqual(self.snapshot(), before)
        result = self.start(NAMES + ('extra.example.test',), role='check')
        self.assertEqual(result.returncode, 1)
        self.assertIn('not valid for extra.example.test', result.stderr)

    def test_an_unknown_role_stops_before_anything(self):
        result = self.start(role='admin')
        self.assertEqual(result.returncode, 1)
        self.assertIn('invalid PLATFORM_TLS_ROLE', result.stderr)
        self.assertEqual(list(self.volume.iterdir()), [])

    def test_an_unknown_mode_stops_nginx(self):
        (self.volume / 'tls-mode').write_text('custom\n')
        result = self.start()
        self.assertEqual(result.returncode, 1)
        self.assertIn('unknown TLS mode', result.stderr)
        self.assertFalse((self.volume / 'ca.key').exists())


if __name__ == '__main__':
    unittest.main()
