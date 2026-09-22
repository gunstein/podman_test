"""Direct developer lifecycle. No systemd units and no forced volume removal."""
import hashlib
import json
from pathlib import Path

from . import apps
from .commands import exists, run
from .install import setup_roles


def up(rendered_manifest_dir, applications=None, state_file=None, refresh=False):
    applications = apps.APPS if applications is None else tuple(applications)
    directory = Path(rendered_manifest_dir)
    state_file = Path(state_file or directory.parent / '.todo-installer-dev.json')
    manifests = [app.manifest(component) for app in applications
                 for component in ('postgres', 'config', 'app')] + ['keycloak.yaml', 'shared-proxy.yaml']
    digest = hashlib.sha256()
    for name in manifests:
        digest.update(name.encode() + b'\0' + (directory / name).read_bytes())
    fingerprint = digest.hexdigest()
    pods = [app.resource(component) for app in applications for component in ('postgres', 'app')]
    pods += ['keycloak', 'shared-proxy']
    previous = json.loads(state_file.read_text()) if state_file.is_file() else None
    present = {pod for pod in pods if exists('pod', pod)}
    if previous and previous['fingerprint'] == fingerprint and not refresh and len(present) == len(pods):
        if all(run('podman', 'pod', 'inspect', '--format', '{{.State}}', pod).stdout.strip()
               == 'Running' for pod in pods):
            return False
    if present and not previous:
        raise RuntimeError('Existing development pods have no installer state. Run down before install.')
    if previous:
        for name in previous['manifests']:
            manifest = Path(name)
            if manifest.is_file():
                run('podman', 'kube', 'play', '--down', manifest)
        state_file.unlink(missing_ok=True)
    if not exists('network', apps.NETWORK):
        run('podman', 'network', 'create', apps.NETWORK)

    def play(manifest, config=None, ports=()):
        arguments = ['--configmap', directory / config] if config else []
        run('podman', 'kube', 'play', '--no-pod-prefix', '--network', apps.NETWORK,
            *arguments, *ports, directory / manifest)

    for app in applications:
        play(app.manifest('postgres'), app.manifest('config'))
        run('podman', 'wait', '--condition', 'healthy', app.resource('postgres'))
        setup_roles(app)
    play('keycloak.yaml')
    for app in applications:
        play(app.manifest('app'), app.manifest('config'))
    play('shared-proxy.yaml', ports=(
        '--publish', '127.0.0.1:8080:8080', '--publish', '127.0.0.1:8443:8443'))
    for app in applications:
        setup_roles(app)
    teardown = ['shared-proxy.yaml', *(app.manifest('app') for app in reversed(applications)),
                'keycloak.yaml', *(app.manifest('postgres') for app in reversed(applications))]
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({'fingerprint': fingerprint,
                                     'manifests': [str(directory / name) for name in teardown]}))
    return True


def down(rendered_manifest_dir, applications=None):
    applications = apps.APPS if applications is None else tuple(applications)
    directory = Path(rendered_manifest_dir)
    manifests = ('shared-proxy.yaml', *(app.manifest('app') for app in reversed(applications)),
                 'keycloak.yaml', *(app.manifest('postgres') for app in reversed(applications)))
    for name in manifests:
        manifest = directory / name
        if manifest.is_file():
            run('podman', 'kube', 'play', '--down', manifest)
