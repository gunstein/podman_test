"""The units rendered from deploy/quadlet against the model's workloads (Platform.workloads()).

One template per kind of workload; each unit's files and Requires= come
from its Workload, so this test checks that the model and the units agree.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, bundle, platform_file, quadlet, workloads  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def unit_lines(text):
    """The unit's settings as {key: [values]}, for the keys this test reads."""
    found = {}
    for line in text.splitlines():
        key, _, value = line.partition('=')
        if key in ('Requires', 'After', 'Yaml', 'ConfigMap'):
            found.setdefault(key, []).extend(value.split())
    return found


class WorkloadUnitTests(unittest.TestCase):
    def rendered(self, workload, platform):
        """workload's unit as a bundle renders it."""
        return unit_lines(bundle.quadlets(ROOT, platform, 8443, '127.0.0.1')[workload.unit].decode())

    def test_every_unit_needs_only_workloads_that_start_before_it(self):
        for platform in (platform_file.checkout(), platform_file.checkout().select(['todo'])):
            order = [workload.service for workload in platform.workloads()]
            for index, workload in enumerate(platform.workloads()):
                with self.subTest(unit=workload.unit, apps=[app.name for app in platform.apps]):
                    lines = self.rendered(workload, platform)
                    for key in ('Requires', 'After'):
                        for service in lines.get(key, []):
                            self.assertIn(service, order[:index], f'{key}={service}')
                    self.assertEqual(lines.get('Requires', []), lines.get('After', []))

    def test_every_unit_runs_its_own_kube_yaml_and_configmap(self):
        for workload in platform_file.checkout().workloads():
            with self.subTest(unit=workload.unit):
                lines = self.rendered(workload, platform_file.checkout())
                self.assertEqual(lines['Yaml'], [workload.yaml])
                self.assertEqual(lines.get('ConfigMap', []), [workload.config] if workload.config else [])

    def test_every_unit_template_serves_a_kind_of_workload(self):
        templates = {path.name.removesuffix('.j2') for path in (ROOT / 'deploy/quadlet').glob('*.kube.j2')}
        self.assertEqual(templates, {workload.template for workload in platform_file.checkout().workloads()})
        self.assertEqual(templates, {'app.kube', 'postgres.kube', 'keycloak.kube', 'shared-proxy.kube'})

    def test_requires_come_from_the_model_and_name_only_earlier_workloads(self):
        platform = platform_file.checkout()
        started = []
        for workload in platform.workloads():
            with self.subTest(unit=workload.unit):
                self.assertTrue(set(workload.requires) <= set(started), workload.requires)
                lines = self.rendered(workload, platform)
                self.assertEqual(lines.get('Requires', []), list(workload.requires))
            started.append(workload.service)
        todo = platform.app('todo')
        self.assertEqual(apps.app_workload(todo).requires, ('todo-postgres.service', 'keycloak.service'))
        # An app without a database or login needs neither, and nginx needs no Keycloak without login.
        help_ = apps.App(name='help', hostname='help.test', has_database=False, replication_port=0)
        static = apps.Platform(apps=(help_,), identity_hostname='auth.test')
        self.assertEqual([(w.pod, w.requires) for w in static.workloads()],
                         [('help-app', ()), ('shared-proxy', ('help-app.service',))])
        lines = unit_lines(quadlet.render(ROOT, 'app.kube', workloads.app_variables(help_)).decode())
        self.assertNotIn('Requires', lines)


if __name__ == '__main__':
    unittest.main()
