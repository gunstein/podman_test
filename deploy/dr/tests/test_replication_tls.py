"""Replication TLS: real openssl for the CA and certificates, fakes for Podman and SQL."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / 'installer')]
from app_dr_host import replication_tls  # noqa: E402
from app_installer import apps, commands  # noqa: E402

APP = apps.APPS[0]


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', str(self.directory)], check=True))
        self.ca_key, self.ca_certificate = replication_tls.make_ca(self.directory)

    def test_a_host_certificate_names_its_address_and_the_database_containers(self):
        key, certificate = replication_tls.issue(self.directory, self.ca_key, self.ca_certificate, '192.0.2.10')
        self.assertTrue(replication_tls.certificate_ok(certificate, self.ca_certificate, '192.0.2.10'))
        self.assertFalse(replication_tls.certificate_ok(certificate, self.ca_certificate, '192.0.2.11'))
        self.assertFalse(replication_tls.certificate_ok(certificate, self.ca_certificate, '192.0.2.1'))
        names = commands.run('openssl', 'x509', '-in', str(certificate), '-noout',
                             '-ext', 'subjectAltName').stdout
        for database in apps.REPLICATED_DATABASES:
            self.assertIn(f'DNS:{database.resource("postgres")}', names)
        self.assertIn('BEGIN PRIVATE KEY', key.read_text())

    def test_another_ca_or_a_certificate_about_to_expire_is_not_kept(self):
        other = self.directory / 'other'
        other.mkdir()
        _, other_ca = replication_tls.make_ca(other)
        _, certificate = replication_tls.issue(self.directory, self.ca_key, self.ca_certificate, '192.0.2.10')
        self.assertFalse(replication_tls.certificate_ok(certificate, other_ca, '192.0.2.10'))
        with patch.object(replication_tls, 'SERVER_DAYS', 1):
            _, short = replication_tls.issue(self.directory, self.ca_key, self.ca_certificate, '192.0.2.10')
        self.assertFalse(replication_tls.certificate_ok(short, self.ca_certificate, '192.0.2.10'))


class EnsureCaTests(unittest.TestCase):
    def test_both_secrets_are_created_once_and_half_a_ca_is_refused(self):
        created = []

        def run(*argv, **kwargs):
            if argv[0] == 'openssl':
                return commands.run(*argv, **kwargs)
            self.assertEqual(argv[:3], ('podman', 'secret', 'create'))
            created.append(argv[3])
            self.assertIn('BEGIN', Path(argv[4]).read_text())
            return subprocess.CompletedProcess(argv, 0, '', '')

        with patch.object(replication_tls, 'run', side_effect=run), \
                patch.object(replication_tls, 'exists', return_value=False):
            self.assertTrue(replication_tls.ensure_ca())
        self.assertEqual(created, list(apps.REPLICATION_CA_SECRETS))
        with patch.object(replication_tls, 'run') as run, \
                patch.object(replication_tls, 'exists', return_value=True):
            self.assertFalse(replication_tls.ensure_ca())
            run.assert_not_called()
        with patch.object(replication_tls, 'run') as run, \
                patch.object(replication_tls, 'exists', side_effect=lambda kind, name: name.endswith('key')), \
                self.assertRaisesRegex(RuntimeError, 'half of the replication CA'):
            replication_tls.ensure_ca()


class InstallServerTlsTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', str(self.directory)], check=True))
        key, certificate = replication_tls.make_ca(self.directory)
        self.ca = {apps.REPLICATION_CA_SECRETS[0]: key.read_text().strip(),
                   apps.REPLICATION_CA_SECRETS[1]: certificate.read_text().strip()}
        self.container = {}

    def install(self, settings):
        writes, statements = [], []

        def run(*argv, input=None, allowed=(0,), timeout=None):
            if argv[0] == 'openssl':
                return commands.run(*argv, allowed=allowed)
            if argv[:3] == ('podman', 'exec', APP.resource('postgres')) and argv[3] == 'cat':
                return subprocess.CompletedProcess(argv, 0 if 'crt' in self.container else 1,
                                                   self.container.get('crt', ''), '')
            self.assertEqual(argv[:6], ('podman', 'exec', '-i', APP.resource('postgres'), 'sh', '-c'))
            target = argv[6].rsplit('/', 1)[1]
            writes.append(target)
            self.container['crt' if target == 'server.crt' else 'key'] = input
            return subprocess.CompletedProcess(argv, 0, '', '')

        def sql(app, statement):
            statements.append(statement)
            if 'ssl_min_protocol_version' in statement and statement.startswith('SELECT'):
                return settings
            return 'on' if statement == "SELECT current_setting('ssl');" else ''

        with patch.object(replication_tls, 'run', side_effect=run), \
                patch.object(replication_tls.secrets, 'read', side_effect=self.ca.__getitem__), \
                patch('app_dr_host.replication.sql', side_effect=sql):
            changed = replication_tls.install_server_tls(APP, '192.0.2.10')
        return changed, writes, statements

    def test_a_new_primary_gets_a_certificate_and_tls_on(self):
        changed, writes, statements = self.install('off|TLSv1.2')
        self.assertTrue(changed)
        self.assertEqual(writes, ['server.key', 'server.crt'])
        self.assertIn("ALTER SYSTEM SET ssl = 'on';", statements)
        self.assertIn("ALTER SYSTEM SET ssl_min_protocol_version = 'TLSv1.2';", statements)
        self.assertIn('SELECT pg_reload_conf();', statements)

    def test_a_valid_certificate_and_tls_on_change_nothing(self):
        self.install('off|TLSv1.2')
        changed, writes, statements = self.install('on|TLSv1.2')
        self.assertFalse(changed)
        self.assertEqual(writes, [])
        self.assertNotIn('SELECT pg_reload_conf();', statements)

    def test_a_certificate_for_another_address_is_replaced(self):
        # After promotion the data directory holds the old primary's certificate.
        _, certificate = replication_tls.issue(
            self.directory, self.directory / 'ca.key', self.directory / 'ca.crt', '192.0.2.99')
        self.container['crt'] = certificate.read_text()
        changed, writes, _ = self.install('on|TLSv1.2')
        self.assertTrue(changed)
        self.assertEqual(writes, ['server.key', 'server.crt'])

    def test_tls_that_never_comes_on_is_an_error(self):
        def sql(app, statement):
            return 'off|TLSv1.2' if 'ssl_min_protocol_version' in statement else 'off'

        with patch.object(replication_tls, 'run', side_effect=lambda *argv, **kw: (
                    commands.run(*argv, **kw) if argv[0] == 'openssl'
                    else subprocess.CompletedProcess(argv, 1, '', ''))), \
                patch.object(replication_tls.secrets, 'read', side_effect=self.ca.__getitem__), \
                patch('app_dr_host.replication.sql', side_effect=sql), \
                patch.object(replication_tls.time, 'sleep'), \
                self.assertRaisesRegex(RuntimeError, 'did not turn TLS on'):
            replication_tls.install_server_tls(APP, '192.0.2.10')


if __name__ == '__main__':
    unittest.main()
