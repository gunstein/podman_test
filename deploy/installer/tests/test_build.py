import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from todo_installer import install  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class BuildInstallTests(unittest.TestCase):
    def test_both_modes_render_with_existing_script_before_installing(self):
        for mode, profile, output in [('server', 'prod', 'kube-runtime'), ('dev', 'local', 'dev')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                shutil.copytree(ROOT / 'deploy/quadlet', root / 'deploy/quadlet')
                directory = root / 'quadlet'
                runtime = directory / 'todo-kube-runtime'
                calls = []
                known_images = set()

                def command(argv, **kwargs):
                    calls.append(argv)
                    stdout, rc = '', 0
                    if argv[0].endswith('render-kube-runtime.sh'):
                        target = Path(argv[-1])
                        target.mkdir(parents=True)
                        for name in ('postgres', 'app', 'keycloak', 'shared-proxy', 'config',
                                     'notes-app', 'notes-postgres', 'notes-config',
                                     'keycloak-postgres', 'keycloak-config'):
                            (target / (name + '.yaml')).write_text('fixture: true\n')
                    elif argv == ['podman', 'kube', 'play', '--help']:
                        stdout = '--no-pod-prefix'
                    elif argv[:3] == ['podman', 'image', 'exists']:
                        rc = int(argv[3] not in known_images)
                    elif argv[:3] == ['podman', 'pod', 'exists']:
                        rc = 1
                    elif argv[:2] == ['podman', 'build']:
                        known_images.add(argv[argv.index('--tag') + 1])
                    elif argv[:2] == ['podman', 'pull']:
                        known_images.add(argv[-1])
                    elif argv[:3] == ['podman', 'image', 'inspect']:
                        stdout = '[{"Labels":{"io.todo.proxy":"nginx"}}]'
                    elif argv[:3] == ['podman', 'secret', 'inspect']:
                        stdout = 'fixture-password'
                    elif argv[:3] == ['systemctl', '--user', 'show']:
                        stdout = str(runtime / argv[3].replace('.service', '.kube'))
                    return subprocess.CompletedProcess(argv, rc, stdout, '')

                with patch('subprocess.run', side_effect=command), \
                        patch('todo_installer.keycloak.configure'):
                    install.install(root, mode=mode, quadlet_dir=directory)
                render = [str(root / 'deploy/scripts/render-kube-runtime.sh'),
                          str(root / f'deploy/environments/{profile}/values.yaml'),
                          str(root / 'generated' / output)]
                self.assertIn(render, calls)
                self.assertEqual(sum(a[:2] == ['podman', 'build'] for a in calls), 6)
                self.assertEqual(sum(a[:2] == ['podman', 'pull'] for a in calls), 1)
                self.assertLess(calls.index(render), next(i for i, a in enumerate(calls)
                                                         if a[:2] == ['podman', 'build']))
