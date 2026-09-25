"""Every check that guards a data deletion must refuse on its own, before anything is deleted.

Each test starts from a host where the guarded operation would go ahead, breaks
exactly one condition, and checks that the operation refuses with a clear
message and that no deleting or changing command ran.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_installer import apps, replication

APP = apps.APPS[1]
HOST = replication.socket.gethostname()
CONFIRMED = dict(confirm_fenced=HOST + ' is fenced', confirm_reseed=HOST)
READ_ONLY = {('podman', 'info'), ('podman', 'ps'), ('podman', 'kube', 'play', '--help')}


def done(stdout='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, '')


class ReseedHost:
    """A rebuild host where reseeding APP would go ahead; tests break one condition each."""

    def __init__(self, root):
        self.quadlet = root / 'quadlet'
        self.runtime = self.quadlet / 'todo-kube-runtime'
        self.runtime.mkdir(parents=True)
        (self.runtime / APP.manifest('config')).write_bytes(b'---\n')
        (self.runtime / APP.manifest('postgres')).write_text(json.dumps({
            'kind': 'PersistentVolumeClaim', 'metadata': {'name': APP.volume('data')}}))
        self.source = root / 'source'
        (self.source / 'deploy/quadlet').mkdir(parents=True)
        (self.source / 'deploy/quadlet/app-network.network').write_bytes(b'')
        (self.source / 'deploy/quadlet' / (APP.unit('postgres') + '.j2')).write_text(
            '{{ postgres_publish_address }}:{{ postgres_publish_port }}')
        self.rootless, self.running, self.pod_prefix = 'true', '', True
        self.missing = set()
        self.commands = []

    def paths(self):
        return dict(project_root=str(self.source), quadlet_dir=str(self.quadlet),
                    kube_runtime_dir=str(self.runtime), rendered_manifest_dir=str(self.runtime))

    def run(self, *argv, **kwargs):
        argv = tuple(str(arg) for arg in argv)
        self.commands.append(argv)
        if argv[:2] == ('podman', 'info'):
            return done(self.rootless)
        if argv[:2] == ('podman', 'ps'):
            return done(self.running)
        if argv[:4] == ('podman', 'kube', 'play', '--help'):
            return done('--no-pod-prefix' if self.pod_prefix else '--replace')
        return done()

    def exists(self, kind, name):
        return (kind, name) not in self.missing

    def reseed(self, primary='192.0.2.10'):
        """Run the real reseed_standby: reseed_check, then authentication, then deletion."""
        with patch.object(replication, 'run', side_effect=self.run), \
                patch.object(replication, 'exists', side_effect=self.exists), \
                patch.object(replication, 'require_stopped_service') as stopped, \
                patch.object(replication, 'authenticate') as authenticate, \
                patch.object(replication, 'bootstrap_standby') as bootstrap:
            try:
                replication.reseed_standby(APP, primary, **CONFIRMED, **self.paths())
            finally:
                self.authenticated = authenticate.called
                self.bootstrapped = bootstrap.called
                self.stopped_checks = [call.args for call in stopped.call_args_list]

    def check(self):
        """Run the real reseed_check alone."""
        with patch.object(replication, 'run', side_effect=self.run), \
                patch.object(replication, 'exists', side_effect=self.exists), \
                patch.object(replication, 'require_stopped_service'):
            return replication.reseed_check(APP, '192.0.2.10', **CONFIRMED, **self.paths())


class ReseedCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.host = ReseedHost(Path(temporary.name))

    def assert_refused(self, message, error=RuntimeError, **reseed):
        with self.assertRaisesRegex(error, message):
            self.host.reseed(**reseed)
        self.assertFalse(self.host.authenticated, 'the primary was contacted')
        self.assertFalse(self.host.bootstrapped, 'a new standby was started')
        changing = [argv for argv in self.host.commands
                    if not any(argv[:len(prefix)] == prefix for prefix in READ_ONLY)]
        self.assertEqual(changing, [], 'a command that changes the host ran')

    def test_the_healthy_host_reseeds_in_order(self):
        self.host.reseed()
        self.assertTrue(self.host.authenticated)
        self.assertEqual(self.host.commands, [
            ('podman', 'info', '--format', '{{.Host.Security.Rootless}}'),
            ('podman', 'ps', '--filter', f"name=^{APP.resource('postgres')}$", '--format', '{{.Names}}'),
            ('podman', 'kube', 'play', '--help'),
            ('podman', 'ps', '-a', '--filter', f"volume={APP.volume('data')}", '--format', '{{.Names}}|{{.State}}'),
            ('podman', 'volume', 'rm', APP.volume('data'))])
        self.assertTrue(self.host.bootstrapped)
        self.assertEqual(self.host.stopped_checks, [(APP.service('postgres'),)])

    def test_the_check_itself_changes_nothing(self):
        self.assertIs(self.host.check(), False)

    def test_wrong_confirmations(self):
        for confirmations in (dict(confirm_fenced=HOST, confirm_reseed=HOST),
                              dict(confirm_fenced=HOST + ' is fenced', confirm_reseed='other-host')):
            with self.subTest(**confirmations), patch.object(replication, 'run') as run, \
                    self.assertRaisesRegex(RuntimeError, 'confirmations are required'):
                replication.reseed_standby(APP, '192.0.2.10', **confirmations, **self.host.paths())
            run.assert_not_called()

    def test_primary_address_must_be_a_literal_ipv4_address(self):
        self.assert_refused('octets', ValueError, primary='primary.example')

    def test_podman_must_be_rootless(self):
        self.host.rootless = 'false'
        self.assert_refused('requires rootless Podman')

    def test_postgres_must_not_be_running(self):
        self.host.running = APP.resource('postgres')
        self.assert_refused('PostgreSQL is still running')

    def test_every_required_object_must_exist(self):
        for missing in (('volume', APP.volume('data')), ('image', APP.image('postgres')),
                        ('secret', APP.secret('replicator')), ('secret', APP.secret('db'))):
            with self.subTest(missing=missing):
                self.host.missing, self.host.commands = {missing}, []
                self.assert_refused(f'required {missing[0]} {missing[1]} is missing; data was not removed')

    def test_a_legacy_container_quadlet_is_refused(self):
        (self.host.quadlet / (APP.resource('postgres') + '.container')).write_text('')
        self.assert_refused('legacy PostgreSQL container Quadlet')

    def test_podman_must_support_no_pod_prefix(self):
        self.host.pod_prefix = False
        self.assert_refused('--no-pod-prefix')

    def test_the_rendered_data_claim_must_exist(self):
        (self.host.runtime / APP.manifest('postgres')).write_text(json.dumps({'kind': 'ConfigMap'}))
        self.assert_refused('exactly one canonical data PVC', ValueError)

    def test_the_kube_runtime_directory_must_be_the_real_one(self):
        self.host.runtime.rename(self.host.quadlet / 'elsewhere')
        self.host.runtime.symlink_to(self.host.quadlet / 'elsewhere')
        self.assert_refused('kube_runtime_dir must be', ValueError)


class ReseedGroupQuarantineTests(unittest.TestCase):
    def test_a_running_container_stops_the_group_before_any_check_or_deletion(self):
        def run(*argv, **kwargs):
            return done('keycloak\n' if argv[:2] == ('podman', 'ps') else '')

        with patch.object(replication, 'run', side_effect=run) as commands, \
                patch.object(replication, 'require_stopped_service'), \
                patch.object(replication, 'reseed_check') as check, \
                patch.object(replication, 'reseed_standby') as reseed:
            with self.assertRaisesRegex(RuntimeError, 'keep infrastructure quarantine'):
                replication.reseed_group('192.0.2.10', **CONFIRMED)
        check.assert_not_called()
        reseed.assert_not_called()
        self.assertFalse([c.args for c in commands.call_args_list if 'rm' in c.args])

    def test_a_service_that_is_not_stopped_stops_the_group(self):
        with patch.object(replication, 'run', return_value=done('LoadState=loaded\nActiveState=active\n'
                                                                'MainPID=42\nControlPID=0\n')), \
                patch.object(replication, 'reseed_standby') as reseed:
            with self.assertRaisesRegex(RuntimeError, 'zero MainPID/ControlPID'):
                replication.reseed_group('192.0.2.10', **CONFIRMED)
        reseed.assert_not_called()


class RebuildPrimaryCheckTests(unittest.TestCase):
    """The current primary must be ready before the old primary's data is replaced."""

    def check(self, *, active='active', missing=(), role='t', slots='0'):
        def run(*argv, **kwargs):
            return done(active if argv[:3] == ('systemctl', '--user', 'is-active') else '')

        def sql(app, statement, **kwargs):
            return role if 'rolreplication' in statement else slots

        with patch.object(replication, 'require_primary'), \
                patch.object(replication, 'run', side_effect=run), \
                patch.object(replication, 'exists', side_effect=lambda kind, name: (kind, name) not in missing), \
                patch.object(replication, 'sql', side_effect=sql):
            return replication.rebuild_primary_check(APP)

    def test_a_ready_primary_passes_without_changes(self):
        self.assertFalse(self.check())

    def test_each_missing_condition_refuses(self):
        cases = [(dict(active='inactive'), 'service is not active'),
                 (dict(missing={('secret', APP.secret('replicator'))}), 'required secret'),
                 (dict(missing={('volume', APP.volume('backup'))}), 'required volume'),
                 (dict(role='f'), 'replication role is missing'),
                 (dict(role=''), 'replication role is missing'),
                 (dict(slots='1'), 'never retry a partial rebuild')]
        for arguments, message in cases:
            with self.subTest(**{key: str(value) for key, value in arguments.items()}):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.check(**arguments)

    def test_a_standby_is_refused_before_anything_else(self):
        with patch.object(replication, 'require_primary', side_effect=RuntimeError('expected a writable primary')), \
                patch.object(replication, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'writable primary'):
                replication.rebuild_primary_check(APP)
        run.assert_not_called()


class RequirePromotedGroupTests(unittest.TestCase):
    """The application tier and backups start only on a completely promoted group."""

    NAMES = [database.name for database in apps.REPLICATED_DATABASES]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.journal = Path(temporary.name) / 'promotion.json'
        self.journal.write_text(json.dumps({'state': 'complete', 'applications': self.NAMES,
                                            'completed': self.NAMES}))

    def check(self, *, active='active', health='healthy', primary=None):
        def run(*argv, **kwargs):
            return done(active if 'is-active' in argv else health)

        with patch.object(replication, 'run', side_effect=run), \
                patch.object(replication, 'require_primary', side_effect=primary):
            return replication.require_promoted_group(self.journal)

    def test_a_complete_healthy_group_passes_without_changes(self):
        self.assertFalse(self.check())

    def test_a_missing_or_unreadable_record_refuses(self):
        for content in (None, 'not json'):
            with self.subTest(content=content):
                if content is None:
                    self.journal.unlink(missing_ok=True)
                else:
                    self.journal.write_text(content)
                with self.assertRaisesRegex(RuntimeError, 'readable completed group promotion record'):
                    self.check()

    def test_a_record_for_a_different_group_refuses(self):
        self.journal.write_text(json.dumps({'state': 'complete', 'applications': self.NAMES[::-1],
                                            'completed': self.NAMES[::-1]}))
        with self.assertRaisesRegex(RuntimeError, 'complete database group'):
            self.check()

    def test_an_inactive_unhealthy_or_read_only_database_refuses(self):
        cases = [(dict(active='failed'), 'database service is not active'),
                 (dict(health='starting'), 'database is not healthy'),
                 (dict(primary=RuntimeError('expected a writable primary')), 'writable primary')]
        for arguments, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                self.check(**arguments)


if __name__ == '__main__':
    unittest.main()
