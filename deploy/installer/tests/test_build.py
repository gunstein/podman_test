import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import install  # noqa: E402
from fake_host import FakeHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class RenderingHost(FakeHost):
    """A host where the render script writes the manifests an install reads."""

    def answer(self, argv, input):
        if argv[0].endswith('render-kube-runtime.sh'):
            target = Path(argv[-1])
            target.mkdir(parents=True)
            for name in ('postgres', 'app', 'keycloak', 'shared-proxy', 'config',
                         'notes-app', 'notes-postgres', 'notes-config',
                         'keycloak-postgres', 'keycloak-config'):
                (target / (name + '.yaml')).write_text('fixture: true\n')
            return 0, ''
        return super().answer(argv, input)


class BuildInstallTests(unittest.TestCase):
    def test_both_modes_render_with_existing_script_before_installing(self):
        for mode, profile, output in [('server', 'prod', 'kube-runtime'), ('dev', 'local', 'dev')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                shutil.copytree(ROOT / 'deploy/quadlet', root / 'deploy/quadlet')
                directory = root / 'quadlet'
                runtime = directory / 'todo-kube-runtime'
                with RenderingHost(images_present=False, unit_directory=runtime) as host, \
                        patch('app_installer.keycloak.configure'):
                    install.install(root, mode=mode, quadlet_dir=directory)
                calls = host.calls
                render = [str(root / 'deploy/scripts/render-kube-runtime.sh'),
                          str(root / f'deploy/environments/{profile}/values.yaml'),
                          str(root / 'generated' / output)]
                self.assertIn(render, calls)
                self.assertEqual(sum(a[:2] == ['podman', 'build'] for a in calls), 6)
                self.assertEqual(sum(a[:2] == ['podman', 'pull'] for a in calls), 1)
                self.assertLess(calls.index(render), next(i for i, a in enumerate(calls)
                                                         if a[:2] == ['podman', 'build']))

    def test_a_refresh_pulls_the_shared_postgres_image_once_for_every_app(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / 'deploy/quadlet', root / 'deploy/quadlet')
            directory = root / 'quadlet'
            with RenderingHost(unit_directory=directory / 'todo-kube-runtime') as host, \
                    patch('app_installer.keycloak.configure'):
                install.install(root, mode='server', quadlet_dir=directory, refresh_images=True)
            self.assertEqual(host.ran('podman', 'pull'), [['podman', 'pull', 'docker.io/library/postgres:17.11']])
            self.assertEqual(len(host.ran('podman', 'build')), 6)
