"""Direct developer lifecycle. No systemd units and no forced volume removal."""
import hashlib
import json
from pathlib import Path

from . import apps, settings
from .commands import exists, run
from .install import setup_roles


def up(rendered_manifest_dir, applications=None, state_file=None, refresh=False):
    applications = apps.APPS if applications is None else tuple(applications)
    directory = Path(rendered_manifest_dir)
    state_file = Path(state_file or settings.DEV_STATE_FILE)
    manifests = [app.manifest(component) for app in applications
                 for component in ('postgres', 'config', 'app')] + [
        apps.KEYCLOAK_DATABASE.manifest('postgres'), apps.KEYCLOAK_DATABASE.manifest('config'),
        'keycloak.yaml', 'shared-proxy.yaml']
    digest = hashlib.sha256()
    for name in manifests:
        digest.update(name.encode() + b'\0' + (directory / name).read_bytes())
    fingerprint = digest.hexdigest()
    pods = [app.resource(component) for app in applications for component in ('postgres', 'app')]
    pods += [apps.KEYCLOAK_DATABASE.resource('postgres'), 'keycloak', 'shared-proxy']
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
    play(apps.KEYCLOAK_DATABASE.manifest('postgres'), apps.KEYCLOAK_DATABASE.manifest('config'))
    run('podman', 'wait', '--condition', 'healthy', apps.KEYCLOAK_DATABASE.resource('postgres'))
    play('keycloak.yaml')
    for app in applications:
        play(app.manifest('app'), app.manifest('config'))
    play('shared-proxy.yaml', ports=(
        '--publish', f'127.0.0.1:{settings.LOCAL_HTTP_PORT}:{settings.LOCAL_HTTP_PORT}',
        '--publish', f'127.0.0.1:{settings.HTTPS_PORT}:{settings.HTTPS_PORT}'))
    for app in applications:
        setup_roles(app)
    teardown = ['shared-proxy.yaml', *(app.manifest('app') for app in reversed(applications)),
                'keycloak.yaml', apps.KEYCLOAK_DATABASE.manifest('postgres'),
                *(app.manifest('postgres') for app in reversed(applications))]
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({'fingerprint': fingerprint,
                                     'manifests': [str(directory / name) for name in teardown]}))
    return True


def down(rendered_manifest_dir, applications=None, state_file=None):
    """Tear down a dev install; True if anything was actually playing.

    Prefers the exact manifests `up` recorded it played, so a caller that
    passes a different `rendered_manifest_dir` than the one used to install
    (offline installs use the bundle's own `generated/kube-runtime`, not the
    source tree's `generated/dev`) still finds and removes the right pods,
    instead of silently matching nothing.
    """
    directory = Path(rendered_manifest_dir)
    state_file = Path(state_file or settings.DEV_STATE_FILE)
    if state_file.is_file():
        manifests = [Path(name) for name in json.loads(state_file.read_text())['manifests']]
    else:
        applications = apps.APPS if applications is None else tuple(applications)
        manifests = [directory / name for name in (
            'shared-proxy.yaml', *(app.manifest('app') for app in reversed(applications)),
            'keycloak.yaml', apps.KEYCLOAK_DATABASE.manifest('postgres'),
            *(app.manifest('postgres') for app in reversed(applications)))]
    torn_down = False
    for manifest in manifests:
        if manifest.is_file():
            run('podman', 'kube', 'play', '--down', manifest)
            torn_down = True
    state_file.unlink(missing_ok=True)
    return torn_down
