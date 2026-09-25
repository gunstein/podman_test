import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, cli, promoted, secrets  # noqa: E402

CERTIFICATE = '-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----'
ISSUER = 'https://todo.test:8443/auth/realms/todo'


class PromotedHost:
    """Records every step of promoted.deploy in order and answers like a healthy host."""

    def __init__(self, hostname='todo-standby', address='192.0.2.11', missing_secrets=(), workloads_changed=False,
                 images_changed=False, issuers=(ISSUER,), reads=None, promoted=True, legacy=False):
        self.hostname, self.address = hostname, address
        self.missing_secrets = set(missing_secrets)
        self.workloads_changed, self.images_changed = workloads_changed, images_changed
        self.issuers = list(issuers)
        self.reads = reads or {}
        self.promoted, self.legacy = promoted, legacy
        self.steps = []

    def run(self, *argv, input=None, allowed=(0,)):
        self.steps.append(argv)
        output = {('hostname',): self.hostname + '\n',
                  ('podman', 'exec', 'nginx', 'cat', promoted.CA_CERTIFICATE): CERTIFICATE + '\n'}.get(argv, '')
        if argv[:2] == ('ip', '-4'):
            output = f'2: eth0    inet {self.address}/24 scope global eth0\n'
        return subprocess.CompletedProcess(argv, 0, output, '')

    def require_promoted_group(self, journal):
        self.steps.append(('promotion', str(journal)))
        if not self.promoted:
            raise RuntimeError('A readable completed group promotion record is required')

    def preflight(self, directory):
        self.steps.append(('legacy-preflight',))
        if self.legacy:
            raise RuntimeError('Unsupported per-container Quadlets are installed.')

    def install(self, name):
        def install(*args, **kwargs):
            self.steps.append(('install', name, kwargs.get('app', apps.SHARED_RESOURCE_OWNER).name))
            return self.workloads_changed
        return install

    def request(self, path, hostname=None):
        self.steps.append(('request', path, hostname))
        if path == promoted.DISCOVERY:
            return {'issuer': self.issuers.pop(0) if len(self.issuers) > 1 else self.issuers[0]}
        return self.reads.get(hostname, [])

    def patches(self, directory):
        return [
            patch.object(promoted, 'run', self.run),
            patch.object(promoted, 'exists', lambda kind, name: name not in self.missing_secrets),
            patch.object(promoted.replication, 'require_promoted_group', self.require_promoted_group),
            patch.object(promoted.install, 'preflight', self.preflight),
            patch.object(promoted.images, 'prepare_offline_group',
                         lambda bundle: (self.steps.append(('images',)), self.images_changed)[1]),
            patch.object(promoted.workloads, 'install_application', self.install('application')),
            patch.object(promoted.workloads, 'install_keycloak', self.install('keycloak')),
            patch.object(promoted.workloads, 'install_shared_proxy', self.install('shared-proxy')),
            patch.object(promoted.quadlet, 'systemctl', lambda *args: self.steps.append(('systemctl',) + args)),
            patch.object(promoted.keycloak, 'wait',
                         lambda path, *a, hostname=None, **k: self.steps.append(('wait', path, hostname))),
            patch.object(promoted.keycloak, 'request', self.request),
            patch.object(promoted.keycloak, 'configure', lambda *a: (self.steps.append(('clients',)), False)[1]),
            patch.object(promoted.secrets, 'read', lambda name: 'admin-password'),
            patch.object(promoted.time, 'sleep', lambda seconds: None),
        ]


class PromotedDeployTests(unittest.TestCase):
    def deploy(self, host, directory, **overrides):
        options = dict(project_root='/ops', quadlet_dir=Path(directory) / 'systemd', bundle_dir='/bundle',
                       inventory_hostname='todo-standby', node_address='192.0.2.11',
                       journal=Path(directory) / 'promotion.json', config_dir=Path(directory), service_port=8443)
        options.update(overrides)
        with ExitStack() as stack:
            for patcher in host.patches(directory):
                stack.enter_context(patcher)
            return promoted.deploy(**options)

    def index(self, host, step):
        return host.steps.index(step)

    def test_unchanged_repeat_starts_verifies_and_exports_without_a_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            host = PromotedHost()
            self.assertTrue(self.deploy(host, directory))
            certificate = Path(directory) / 'todo-nginx-root.crt'
            self.assertEqual(certificate.read_text(), CERTIFICATE + '\n')
            self.assertEqual(stat.S_IMODE(certificate.stat().st_mode), 0o644)
            host = PromotedHost()
            self.assertFalse(self.deploy(host, directory))
            self.assertFalse([s for s in host.steps if s[:3] == ('systemctl', '--user', 'stop')])
            starts = [s[2] for s in host.steps if s[:2] == ('systemctl', 'start')]
            self.assertEqual(starts, [app.service('app') for app in apps.APPS]
                             + ['keycloak.service', 'shared-proxy.service'])
            for app in apps.APPS:
                self.assertLess(self.index(host, ('systemctl', 'start', 'shared-proxy.service')),
                                self.index(host, ('wait', '/health', app.hostname)))
                self.assertIn(('request', app.api_path(), app.hostname), host.steps)
            self.assertLess(self.index(host, ('request', promoted.DISCOVERY, None)), self.index(host, ('clients',)))

    def test_any_image_or_workload_change_stops_the_whole_tier_once_before_starting(self):
        for changes in ({'workloads_changed': True}, {'images_changed': True}):
            with self.subTest(**changes), tempfile.TemporaryDirectory() as directory:
                host = PromotedHost(**changes)
                self.assertTrue(self.deploy(host, directory))
                stops = [s for s in host.steps if s[:3] == ('systemctl', '--user', 'stop')]
                self.assertEqual(stops, [('systemctl', '--user', 'stop', *apps.services(databases=False))])
                self.assertLess(host.steps.index(stops[0]),
                                min(i for i, s in enumerate(host.steps) if s[:2] == ('systemctl', 'start')))

    def test_every_gate_refuses_before_images_or_workloads_change(self):
        cases = {
            'hostname': (PromotedHost(hostname='todo-primary'), {}, 'hostname'),
            'address': (PromotedHost(address='192.0.2.99'), {}, '192.0.2.11 is not'),
            'port': (PromotedHost(), {'service_port': 443}, 'service port 443'),
            'promotion': (PromotedHost(promoted=False), {}, 'promotion record'),
            'secret': (PromotedHost(missing_secrets=[secrets.replicated_names()[-1]]), {},
                       secrets.replicated_names()[-1]),
        }
        for name, (host, overrides, message) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(RuntimeError, message):
                    self.deploy(host, directory, **overrides)
                self.assertNotIn(('images',), host.steps)
                self.assertFalse([s for s in host.steps if s[0] == 'install'])
                if name in ('hostname', 'address', 'port'):
                    self.assertFalse([s for s in host.steps if s[0] == 'promotion'])

    def test_legacy_quadlets_refuse_before_any_workload_install(self):
        with tempfile.TemporaryDirectory() as directory:
            host = PromotedHost(legacy=True)
            with self.assertRaisesRegex(RuntimeError, 'per-container'):
                self.deploy(host, directory)
            self.assertFalse([s for s in host.steps if s[0] == 'install'])

    def test_installs_every_application_keycloak_and_the_proxy_on_the_node_address(self):
        with tempfile.TemporaryDirectory() as directory:
            host = PromotedHost()
            self.deploy(host, directory)
            installs = [s[1:] for s in host.steps if s[0] == 'install']
            self.assertEqual(installs, [('application', app.name) for app in apps.APPS]
                             + [('keycloak', apps.SHARED_RESOURCE_OWNER.name),
                                ('shared-proxy', apps.SHARED_RESOURCE_OWNER.name)])

    def test_a_failed_public_read_or_foreign_issuer_stops_the_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            host = PromotedHost(reads={apps.APPS[-1].hostname: {'detail': 'error'}})
            with self.assertRaisesRegex(RuntimeError, 'did not return a list'):
                self.deploy(host, directory)
            host = PromotedHost(issuers=['https://192.0.2.11:8443/auth/realms/todo'])
            with self.assertRaisesRegex(RuntimeError, 'canonical'):
                self.deploy(host, directory)
            self.assertNotIn(('clients',), host.steps)

    def test_issuer_is_retried_until_it_settles(self):
        with tempfile.TemporaryDirectory() as directory:
            host = PromotedHost(issuers=['https://localhost/auth/realms/todo', ISSUER])
            self.deploy(host, directory)
            self.assertIn(('clients',), host.steps)

    def test_cli_reports_one_json_result(self):
        with tempfile.TemporaryDirectory() as directory:
            host = PromotedHost()
            output = io.StringIO()
            with ExitStack() as stack:
                for patcher in host.patches(directory):
                    stack.enter_context(patcher)
                stack.enter_context(redirect_stdout(output))
                self.assertEqual(cli.main([
                    'deploy-promoted', '--project-root', '/ops', '--quadlet-dir', directory,
                    '--bundle-dir', '/bundle', '--inventory-hostname', 'todo-standby',
                    '--node-address', '192.0.2.11', '--journal', directory + '/promotion.json',
                    '--config-dir', directory]), 0)
            self.assertEqual(json.loads(output.getvalue()), {'changed': True})


if __name__ == '__main__':
    unittest.main()
