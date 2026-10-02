"""One fake rootless Podman host for the installer tests.

The installer starts every program through commands.run, which calls
subprocess.run; FakeHost replaces that one function while a test runs:

    with FakeHost(unit_directory=runtime) as host:
        install.install(...)
    self.assertIn(['systemctl', '--user', 'start', 'todo-app.service'], host.calls)

It keeps the little a single host has to remember (secrets, images) and
answers the questions the installer asks from that state, so a test only
describes its scenario. Every command is recorded in host.calls, in order:
the order is part of what the installer must get right. A test that needs one
more answer subclasses FakeHost and overrides answer().
"""
import subprocess
from pathlib import Path
from unittest import mock

from app_installer import secrets

# What `podman image inspect` shows for the proxy image the installer checks.
PROXY_LABELS = '[{"Labels":{"io.todo.proxy":"nginx"}}]'


class FakeHost:
    """A single host after the password secrets exist, with no pod running yet.

    password: the value of each raw password secret the installer creates.
    images_present: False starts with no image, so builds, pulls and loads
    happen; an image then exists once it is built or pulled.
    unit_directory: where `systemctl --user show` says each service's unit
    file is; source overrides it with one fixed path.
    """

    def __init__(self, *, password='fixture-password\n', images_present=True,
                 unit_directory=None, source=None):
        self.secrets = {name: password for name in secrets.installed_names()}
        self.images = None if images_present else set()
        self.unit_directory = unit_directory
        self.source = source
        self.calls = []

    def __enter__(self):
        self._patcher = mock.patch('app_installer.commands.subprocess.run', side_effect=self._run)
        self._patcher.start()
        return self

    def __exit__(self, *error):
        self._patcher.stop()

    def _run(self, argv, input=None, **_):
        argv = list(argv)
        self.calls.append(argv)
        rc, stdout = self.answer(argv, input)
        return subprocess.CompletedProcess(argv, rc, stdout, '')

    def answer(self, argv, input):
        """The exit code and output of one command on this host."""
        if argv == ['podman', 'kube', 'play', '--help']:
            return 0, '--no-pod-prefix'
        if argv[:3] == ['podman', 'pod', 'exists']:
            return 1, ''
        if argv[:3] == ['podman', 'secret', 'exists']:
            return int(argv[3] not in self.secrets), ''
        if argv[:3] == ['podman', 'secret', 'inspect']:
            name = argv[-1]
            return (0, self.secrets[name]) if name in self.secrets else (125, '')
        if argv[:3] == ['podman', 'secret', 'create']:
            self.secrets[argv[3]] = input
            return 0, ''
        if argv[:3] == ['podman', 'image', 'exists']:
            return int(self.images is not None and argv[3] not in self.images), ''
        if argv[:3] == ['podman', 'image', 'inspect']:
            return 0, PROXY_LABELS
        if argv[:2] == ['podman', 'build'] and self.images is not None:
            self.images.add(argv[argv.index('--tag') + 1])
        if argv[:2] == ['podman', 'pull'] and self.images is not None:
            self.images.add(argv[-1])
        if argv[:3] == ['systemctl', '--user', 'show']:
            unit = self.source or str(self.unit_directory / argv[3].replace('.service', '.kube'))
            return 0, unit + '\n'
        return 0, ''

    def ran(self, *prefix):
        """The recorded commands that start with prefix."""
        return [argv for argv in self.calls if argv[:len(prefix)] == list(prefix)]


class RenderingHost(FakeHost):
    """A host where the build-mode render script writes the manifests an install reads."""

    def answer(self, argv, input):
        if argv[0].endswith('render-kube-runtime.sh'):  # script, values file, output directory[, apps]
            target = Path(argv[2])
            target.mkdir(parents=True)
            for name in ('postgres', 'app', 'keycloak', 'shared-proxy', 'config',
                         'notes-app', 'notes-postgres', 'notes-config',
                         'keycloak-postgres', 'keycloak-config'):
                (target / (name + '.yaml')).write_text('fixture: true\n')
            return 0, ''
        return super().answer(argv, input)
