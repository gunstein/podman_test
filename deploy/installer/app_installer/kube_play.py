"""Direct developer lifecycle. No systemd units and no forced volume removal.

Only the Kube secrets' volumes go at down (secrets.remove_kube_volumes): copies, not data.
"""
import hashlib
import json
from pathlib import Path

from . import apps, secrets, settings
from .commands import exists, run
from .install import setup_roles


def up(rendered_manifest_dir, applications=None, state_file=None, refresh=False):
    """Start the development stack with podman kube play, without systemd.

    A fingerprint of the rendered YAML is stored in state_file. If nothing
    changed and every pod runs, this does nothing and returns False.
    Otherwise it takes down what it started last time, then starts
    everything in dependency order and records what to tear down: the YAML
    itself, not its path, since the next render replaces the directory.
    Pods that exist without a state file are refused rather than guessed at.
    """
    applications = apps.APPS if applications is None else tuple(applications)
    selected = apps.workloads(applications)
    directory = Path(rendered_manifest_dir)
    state_file = Path(state_file or settings.DEV_STATE_FILE)
    digest = hashlib.sha256()
    for name in dict.fromkeys(name for workload in selected for name in workload.manifests):
        digest.update(name.encode() + b'\0' + (directory / name).read_bytes())
    fingerprint = digest.hexdigest()
    pods = [workload.pod for workload in selected]
    previous = json.loads(state_file.read_text()) if state_file.is_file() else None
    present = {pod for pod in pods if exists('pod', pod)}
    if previous and previous['fingerprint'] == fingerprint and not refresh and len(present) == len(pods):
        if all(run('podman', 'pod', 'inspect', '--format', '{{.State}}', pod).stdout.strip()
               == 'Running' for pod in pods):
            return False
    if present and not previous:
        raise RuntimeError('Existing development pods have no installer state. Run down before install.')
    if previous:
        _tear_down(previous['teardown'])
        state_file.unlink(missing_ok=True)
    if not exists('network', apps.NETWORK):
        run('podman', 'network', 'create', apps.NETWORK)

    def play(manifest, config=None, ports=()):
        """podman kube play one manifest on app-network, with an optional ConfigMap and published ports."""
        arguments = ['--configmap', directory / config] if config else []
        run('podman', 'kube', 'play', '--no-pod-prefix', '--network', apps.NETWORK,
            *arguments, *ports, directory / manifest)

    # Roles are set up once an app's database is healthy, and again after every pod started.
    databases = {app.database.container: app for app in applications}
    for workload in selected:
        if workload.pod == 'shared-proxy':
            # nginx publishes its ports on loopback; its own ConfigMaps are in shared-proxy.yaml.
            play(workload.yaml, ports=(
                '--publish', f'127.0.0.1:{settings.LOCAL_HTTP_PORT}:{settings.LOCAL_HTTP_PORT}',
                '--publish', f'127.0.0.1:{settings.HTTPS_PORT}:{settings.HTTPS_PORT}'))
        else:
            play(workload.yaml, workload.config)
        if workload.wait_healthy:
            run('podman', 'wait', '--condition', 'healthy', workload.pod, timeout=settings.HEALTH_TIMEOUT)
        if workload.pod in databases:
            setup_roles(databases[workload.pod])
    for app in applications:
        setup_roles(app)
    teardown = [workload.yaml for workload in reversed(selected)]
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({'fingerprint': fingerprint,
                                     'teardown': [(directory / name).read_text() for name in teardown]}))
    return True


def _tear_down(manifests):
    """podman kube play --down for each manifest, in order; True if any ran.

    A manifest is YAML text from the state file, passed on stdin, so the
    rendered file need not exist any more; or a Path, skipped if missing.
    """
    torn_down = False
    for manifest in manifests:
        if isinstance(manifest, Path):
            if not manifest.is_file():
                continue
            run('podman', 'kube', 'play', '--down', manifest)
        else:
            run('podman', 'kube', 'play', '--down', '-', input=manifest)
        torn_down = True
    return torn_down


def down(rendered_manifest_dir, applications=None, state_file=None):
    """Tear down a dev install; True if anything was actually playing.

    Prefers the exact manifests `up` recorded it played, so a caller that
    passes a different `rendered_manifest_dir` than the one used to install,
    or a later render that no longer has the file, still removes the right
    pods instead of silently matching nothing.
    """
    directory = Path(rendered_manifest_dir)
    state_file = Path(state_file or settings.DEV_STATE_FILE)
    if state_file.is_file():
        torn_down = _tear_down(json.loads(state_file.read_text())['teardown'])
    else:
        applications = apps.APPS if applications is None else tuple(applications)
        torn_down = _tear_down([directory / workload.yaml for workload in reversed(apps.workloads(applications))])
    state_file.unlink(missing_ok=True)
    # kube down keeps the copies of the passwords kube play made; they go too.
    secrets.remove_kube_volumes()
    return torn_down
