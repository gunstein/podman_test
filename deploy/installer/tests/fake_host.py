"""One fake rootless Podman host for the installer tests.

The installer starts every program through commands.run, which calls
subprocess.run; FakeHost replaces that one function while a test runs:

    with FakeHost(unit_directory=runtime) as host:
        install.install(...)
    self.assertIn(['systemctl', '--user', 'start', 'todo-app.service'], host.calls)

It keeps the little a single host has to remember (secrets, images) and
answers the questions the installer asks from that state, so a test only
describes its scenario. Every command is recorded in host.calls, in order:
the order is part of what the installer must get right. The host's record of
its public hostnames (target_render.record_path) is host.record, in a
temporary home. A test that needs one more answer subclasses FakeHost and
overrides answer().
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from app_installer import apps, platform_file, secrets, tls_secrets

# What `podman image inspect` shows for the proxy image the installer checks.
PROXY_LABELS = '[{"Labels":{"io.todo.proxy":"nginx","io.todo.proxy.tls":"local provided"}}]'
# The real subprocess.run, kept before a fake replaces it: what a throwaway
# proxy container would run (tls_secrets.proxy) runs here instead.
REAL_RUN = subprocess.run
# The repository's entrypoint, for tls_secrets.status (patch tls_secrets.ENTRYPOINT with it).
ENTRYPOINT = str(Path(__file__).resolve().parents[3] / 'proxy/proxy-entrypoint.sh')


# RSA keys made once for all tests: a new key per step would only make them slower.
# Each host hands them out in turn (FakeHost.new_key), so its keys differ.
KEY_POOL = []


def pooled_key(index):
    """The index-th key of KEY_POOL, made with this machine's openssl when first needed."""
    while len(KEY_POOL) <= index % 4:
        KEY_POOL.append(REAL_RUN(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048'],
                                 text=True, capture_output=True, check=True).stdout)
    return KEY_POOL[index % 4]


def proxy_container(argv, input, secrets_by_name):
    """Run `podman run ... --secret NAME,...,target=FILE ... --entrypoint /bin/sh IMAGE -c ...` here.

    Each --secret becomes a file in a temporary directory that stands in for
    tls_secrets.MOUNT, holding the secret's value as Podman would mount it;
    the tmpfs /work is another temporary directory, the working directory.
    The shell and openssl are this machine's. Returns (exit code, stdout, stderr);
    a secret that does not exist is exit 125, as with Podman.
    """
    with tempfile.TemporaryDirectory() as directory:
        mount, work = Path(directory) / 'mount', Path(directory) / 'work'
        mount.mkdir()
        work.mkdir()
        for index, word in enumerate(argv):
            if word == '--secret':
                name, *options = argv[index + 1].split(',')
                target = dict(option.split('=', 1) for option in options)['target']
                if name not in secrets_by_name:
                    return 125, '', f'Error: {name}: no such secret'
                (mount / Path(target).name).write_text(secrets_by_name[name])
        script = [word.replace(tls_secrets.MOUNT, str(mount))
                  for word in argv[argv.index('--entrypoint') + 3:]]  # after /bin/sh and the image
        result = REAL_RUN(['sh', *script], cwd=work, input=input, text=True, capture_output=True, check=False)
        return result.returncode, result.stdout, result.stderr


class FakeHost:
    """A single host after the password secrets exist, with no pod running yet.

    password: the value of each raw password secret the installer creates.
    images_present: False starts with no image, so builds, pulls and loads
    happen; an image then exists once it is built or pulled.
    unit_directory: where `systemctl --user show` says each service's unit
    file is; source overrides it with one fixed path.
    """

    def __init__(self, *, password='fixture-password\n', images_present=True,
                 unit_directory=None, source=None, platform=None):
        # The platform this host was installed with: recorded in its home (target_render.record_platform).
        self.platform = platform_file.checkout() if platform is None else platform
        self.secrets = {name: password for name in secrets.installed_names(self.platform)}
        self.volumes = set()
        self.volumes_in_use = set()
        # The files of the TLS volume platform-nginx-data, if a test gives it some ({name: text}).
        self.volume_files = {}
        self.keys_made = 0
        self.images = None if images_present else set()
        self.unit_directory = unit_directory
        self.source = source
        self.calls = []
        self.requests = []  # (hostname, path) of each HTTP check through nginx
        self.timers = set()  # user timers that are enabled and running

    def __enter__(self):
        # The host's record of its public hostnames lives in a temporary
        # directory, never in the home directory of whoever runs the tests.
        self.home = Path(tempfile.mkdtemp())
        self.record = self.home / '.config/platform/target-values.json'
        self.platform_record = self.home / '.config/platform/platform.json'
        self.platform_record.parent.mkdir(parents=True)
        self.platform_record.write_text(json.dumps(self.platform.to_json()))
        # The nightly backup timer's units (backup.install_timer) go here too.
        self.units = self.home / '.config/systemd/user'
        self._patchers = [mock.patch('app_installer.commands.subprocess.run', side_effect=self._run),
                          mock.patch('app_installer.target_render.record_path', return_value=self.record),
                          mock.patch('app_installer.target_render.platform_record_path',
                                     return_value=self.platform_record),
                          mock.patch('app_installer.settings.SYSTEMD_USER_DIR', self.units),
                          # nginx's demo CA and leaf (tls_secrets): smaller keys only to keep the tests fast.
                          mock.patch('app_installer.tls_secrets.KEY_BITS', 2048),
                          mock.patch('app_installer.tls_secrets.ENTRYPOINT', ENTRYPOINT),
                          # Each app's ready path and checks (checks.py) through nginx: every one answers 200.
                          mock.patch('app_installer.checks.status', side_effect=self._status)]
        for patcher in self._patchers:
            patcher.start()
        return self

    def _status(self, path, hostname):
        self.requests.append((hostname, path))
        return 200

    def __exit__(self, *error):
        for patcher in self._patchers:
            patcher.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def _run(self, argv, input=None, **_):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ['podman', 'run'] and '--tmpfs' in argv and 'genpkey' in argv:
            self.keys_made += 1
            return subprocess.CompletedProcess(argv, 0, pooled_key(self.keys_made), '')
        if argv[:2] == ['podman', 'run'] and '--tmpfs' in argv:  # tls_secrets.proxy
            return subprocess.CompletedProcess(argv, *proxy_container(argv, input, self.secrets))
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
            # As Podman 4.9: --replace needs an existing secret, a new name must not exist.
            replace = '--replace' in argv
            name = [word for word in argv[3:] if not word.startswith('--')][0]
            if replace != (name in self.secrets):
                return 125, ''
            self.secrets[name] = input
            return 0, ''
        if argv[:3] == ['podman', 'secret', 'rm']:
            return (0, self.secrets.pop(argv[3]) and '') if argv[3] in self.secrets else (1, '')
        if argv[:3] == ['podman', 'volume', 'exists']:
            return int(argv[3] not in self.volumes), ''
        if argv[:3] == ['podman', 'volume', 'rm']:
            return (0, self.volumes.discard(argv[3]) or '') if argv[3] in self.volumes else (1, '')
        if argv[:4] == ['podman', 'ps', '--all', '--quiet'] and argv[-1].startswith('volume='):
            # The containers that use a volume, from volumes_in_use.
            return 0, 'c0ffee\n' if argv[-1].removeprefix('volume=') in self.volumes_in_use else ''
        if argv[:2] == ['podman', 'run'] and '--volume' in argv and \
                argv[argv.index('--volume') + 1].startswith('platform-nginx-data:'):
            # tls.py's throwaway containers on the TLS volume: only test -s and cat.
            program = argv[argv.index('--entrypoint') + 1]
            words = [program, *argv[argv.index('--entrypoint') + 3:]] if program == 'cat' else \
                argv[argv.index('tls') + 1:]
            text = self.volume_files.get(Path(words[-1]).name)
            if words[0] == 'test':
                return int(not text), ''
            if words[0] == 'cat':
                return (0, text) if text is not None else (1, '')
        if argv[:3] == ['podman', 'image', 'exists']:
            return int(self.images is not None and argv[3] not in self.images), ''
        if argv[:3] == ['podman', 'image', 'inspect']:
            return 0, PROXY_LABELS
        if argv[:2] == ['podman', 'build'] and self.images is not None:
            self.images.add(argv[argv.index('--tag') + 1])
        if argv[:2] == ['podman', 'pull'] and self.images is not None:
            self.images.add(argv[-1])
        if argv[:3] == ['systemctl', '--user', 'is-enabled']:
            return (0, 'enabled') if argv[3] in self.timers else (1, 'disabled')
        if argv[:3] == ['systemctl', '--user', 'is-active'] and argv[3].endswith('.timer'):
            return (0, 'active') if argv[3] in self.timers else (3, 'inactive')
        if argv[:4] == ['systemctl', '--user', 'enable', '--now']:
            self.timers.add(argv[4])
        if argv[:4] == ['systemctl', '--user', 'disable', '--now']:
            self.timers.discard(argv[4])
        if argv[:3] == ['systemctl', '--user', 'show']:
            unit = self.source or str(self.unit_directory / argv[3].replace('.service', '.kube'))
            return 0, unit + '\n'
        return 0, ''

    def ran(self, *prefix):
        """The recorded commands that start with prefix."""
        return [argv for argv in self.calls if argv[:len(prefix)] == list(prefix)]


class RenderingHost(FakeHost):
    """A host where a build-mode install's render (render.render) writes placeholder manifests.

    rendered holds each render's (environment, output directory, platform).
    """

    def __enter__(self):
        self.rendered = []
        super().__enter__()
        patcher = mock.patch('app_installer.render.render', side_effect=self._render)
        patcher.start()
        self._patchers.append(patcher)
        return self

    def _render(self, project_root, environment, output_directory, platform):
        self.rendered.append((environment, Path(output_directory), platform))
        target = Path(output_directory)
        target.mkdir(parents=True)
        names = ['keycloak.yaml', 'shared-proxy.yaml', apps.KEYCLOAK_DATABASE.manifest,
                 apps.KEYCLOAK_DATABASE.config_manifest]
        for app in platform.apps:
            names += [app.manifest, app.database.manifest, app.config_manifest]
        for name in names:
            (target / name).write_text('fixture: true\n')
