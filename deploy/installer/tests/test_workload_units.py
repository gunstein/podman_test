"""The unit templates in deploy/quadlet against platform_file.checkout().workloads().

The units name their dependencies and files literally, which is easiest to
read; this test notices when they and the workload table drift apart.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import platform_file, quadlet, workloads  # noqa: E402

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
        variables = workloads.proxy_variables('127.0.0.1', 8443, platform) \
            if workload.pod == 'shared-proxy' else {}
        return unit_lines(quadlet.render(ROOT, workload.unit, variables).decode())

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

    def test_every_unit_template_is_a_workload(self):
        templates = {path.name.removesuffix('.j2') for path in (ROOT / 'deploy/quadlet').glob('*.kube.j2')}
        self.assertEqual(templates, {workload.unit for workload in platform_file.checkout().workloads()})


if __name__ == '__main__':
    unittest.main()
