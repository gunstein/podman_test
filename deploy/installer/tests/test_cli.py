import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, target_render  # noqa: E402
from app_installer.cli import main  # noqa: E402


def setUpModule():
    # The host commands take the platform the host recorded at install; here, the registry's.
    patcher = patch('app_installer.target_render.installed_platform', return_value=apps.registry())
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class CLITests(unittest.TestCase):
    def test_install_prints_one_json_result(self):
        with patch('app_installer.install.install', return_value=False) as install, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['install', '--project-root', '/source', '--publish-address', '192.0.2.2',
                                   '--service-port', '9443']), 0)
        self.assertEqual(output.getvalue(), '{"changed": false}\n')
        self.assertEqual(install.call_args.args[0], Path('/source'))
        self.assertEqual(install.call_args.args[5:7], ('192.0.2.2', 9443))

    def test_failure_has_no_json_success(self):
        with patch('app_installer.install.install', side_effect=RuntimeError('failed')), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(['install']), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(error.getvalue(), 'app-installer: failed\n')

    def test_only_the_single_host_commands_and_the_registry_remain(self):
        # The DR tools import the installer's functions; nothing runs workload commands.
        for command in ('install-workload', 'configure-clients', 'services'):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                main([command])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['replication-apps']), 0)
        self.assertEqual(json.loads(output.getvalue()), ['todo', 'notes', 'keycloak'])


def run(argv):
    """main(argv): its exit code, stdout and stderr."""
    with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as error:
        code = main(argv)
    return code, output.getvalue(), error.getvalue()


class UninstallCLITests(unittest.TestCase):
    """E3: what uninstall says it kept, for each choice of what to remove."""

    def test_each_choice_says_what_it_kept_or_removed(self):
        for options, said in (([], 'Use --remove-data to delete them permanently'),
                              (['--remove-data'], 'Backup volumes todo-postgres-backup, notes-postgres-backup, '
                                                  'keycloak-postgres-backup were preserved'),
                              (['--remove-data', '--remove-backups'], 'nothing of this install can be restored')):
            with self.subTest(options), patch('app_installer.uninstall.uninstall', return_value=True) as remove:
                code, output, error = run(['uninstall', *options])
            self.assertEqual((code, output), (0, '{"changed": true}\n'))
            self.assertIn(said, error)
            self.assertEqual(remove.call_args.args[0], apps.registry())
            self.assertEqual(remove.call_args.args[1], '--remove-data' in options)
            self.assertEqual(remove.call_args.args[3], '--remove-backups' in options)


class BackupAndTlsCLITests(unittest.TestCase):
    """E3: the backup and tls- commands that print text, and their failures."""

    def test_create_prints_one_line_per_database(self):
        databases = [type('D', (), {'name': name})() for name in ('todo', 'notes')]
        with patch('app_installer.backup.installed_databases', return_value=databases), \
                patch('app_installer.backup.create', side_effect=['base-1', 'base-2']):
            code, output, _ = run(['backup', 'create'])
        self.assertEqual((code, output), (0, 'todo: verified base backup base-1\nnotes: verified base backup base-2\n'))

    def test_nightly_keeps_at_least_a_day_and_reports_a_tls_problem(self):
        with patch('app_installer.backup.nightly') as nightly:
            code, _, error = run(['backup', 'nightly', '--keep-days', '0'])
        self.assertEqual(code, 1)
        self.assertIn('--keep-days must keep at least one day', error)
        nightly.assert_not_called()
        with patch('app_installer.backup.nightly', return_value=(['todo: ok'], [])), \
                patch('app_installer.tls_store.module', side_effect=RuntimeError('podman is gone')):
            code, output, error = run(['backup', 'nightly', '--keep-days', '7'])
        self.assertEqual(code, 1)
        self.assertEqual(output, 'todo: ok\n')
        self.assertIn('ERROR: cannot check the nginx certificate: podman is gone', error)

    def test_tls_status_without_a_certificate_and_renew_on_the_volume(self):
        module = type('M', (), {'check': staticmethod(lambda: ([], []))})
        with patch('app_installer.tls_store.module', return_value=module):
            self.assertEqual(run(['tls-status'])[:2], (0, 'nginx has no certificate yet\n'))
        with patch('app_installer.tls_store.module', return_value=module), \
                patch('app_installer.tls_store.secret_storage', return_value=False), \
                patch('app_installer.tls_secrets.renew') as renew:
            code, _, error = run(['tls-renew'])
        self.assertEqual(code, 1)
        self.assertIn('restart shared-proxy.service renews the demo certificate', error)
        renew.assert_not_called()

    def test_down_says_when_nothing_was_installed(self):
        with tempfile.TemporaryDirectory() as home:
            record = Path(home) / 'platform.json'
            with patch('app_installer.target_render.platform_record_path', return_value=record), \
                    patch('app_installer.kube_play.down', return_value=False) as down:
                # No platform record: nothing was installed.
                code, _, error = run(['down', '--rendered-manifest-dir', '/nowhere'])
                self.assertEqual(code, 0)
                self.assertIn('No installed development manifests were found under /nowhere', error)
                down.assert_not_called()
                # A record, but no manifests.
                record.write_text('{}')
                code, _, error = run(['down', '--rendered-manifest-dir', '/nowhere'])
                self.assertEqual(code, 0)
                self.assertIn('nothing was torn down', error)
                down.assert_called_once()

    def test_down_fails_on_a_broken_platform_record(self):
        with tempfile.TemporaryDirectory() as home:
            record = Path(home) / 'platform.json'
            record.write_text('not json')
            with patch('app_installer.target_render.platform_record_path', return_value=record), \
                    patch('app_installer.target_render.installed_platform',
                          side_effect=target_render.TargetError(f'{record} is not a platform')), \
                    patch('app_installer.kube_play.down') as down:
                code, _, error = run(['down', '--rendered-manifest-dir', '/nowhere'])
        self.assertEqual(code, 1)
        self.assertIn('is not a platform', error)
        down.assert_not_called()
