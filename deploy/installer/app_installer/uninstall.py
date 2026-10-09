"""Single-host uninstall, retaining DR refusal and opt-in database removal."""
import shutil
from pathlib import Path

from . import apps, backup, install, secrets, settings, target_render, tls_secrets
from .commands import exists, run
from .quadlet import systemctl

# caddy-data is a retired Caddy-based proxy's volume name; kept here so a host
# still carrying it from before the nginx migration gets it cleaned up too.
TLS_VOLUMES = (apps.NGINX_TLS_VOLUME, 'todo-caddy-data')
QUADLET_FILES = (apps.NETWORK + '.network',
                 *(app.database.volume(purpose) + '.volume' for app in apps.APPS
                   for purpose in ('data', 'backup')),
                 *(apps.KEYCLOAK_DATABASE.volume(purpose) + '.volume' for purpose in ('data', 'backup')),
                 *(volume + '.volume' for volume in TLS_VOLUMES))
# Every workload's service, the old per-container services of each app, and the network's.
SERVICES = (*(workload.pod for workload in apps.workloads()),
            *(app.names.resource(component) for app in apps.APPS
              for component in ('frontend', 'backend', 'db-grants', 'migrate', 'db-setup')),
            apps.NETWORK + '-network')
CONTAINERS = (*(app.names.resource(component) for app in apps.APPS
                for component in ('frontend', 'backend', 'migrate', 'db-grants', 'db-setup', 'postgres')),
              'nginx', 'keycloak')
PODS = tuple(workload.pod for workload in reversed(apps.workloads()))
MAPPINGS = secrets.kube_mappings()
# nginx's TLS secrets (tls_secrets.py) go with the data, as the TLS volume does.
SECRETS = tuple(dict.fromkeys([*MAPPINGS, *(source for fields in MAPPINGS.values()
                                          for source in fields.values()),
                               *tls_secrets.secret_names()]))


def remove(kind, name):
    """Remove the Podman object if it exists; True if it did."""
    if exists(kind, name):
        run('podman', kind, 'rm', name)
        return True
    return False


def unlink(path):
    """Remove the file or symlink if it exists; True if it did."""
    existed = path.exists() or path.is_symlink()
    path.unlink(missing_ok=True)
    return existed


def uninstall(remove_data=False, quadlet_dir=None):
    """Remove a single-host install: units, pods, containers, network and app images.

    Refuses on a host with replication, promotion or backup state: that is
    a DR node and needs a person to decide. The nightly backup timer goes;
    database volumes, TLS volumes and secrets are kept unless remove_data is
    True, and backup volumes always, so reinstalling keeps the
    data and passwords. The Kube secrets' volumes always go
    (secrets.remove_kube_volumes). Returns True if anything was removed.
    """
    directory = Path(quadlet_dir or settings.QUADLET_DIR)
    install.require_single_host('uninstall')
    stopped = run('systemctl', '--user', 'stop', *(name + '.service' for name in SERVICES),
                  allowed=(0, 5)).returncode == 0
    # The nightly backup timer goes; the backups themselves stay in their volumes.
    changed = backup.remove_timer() or stopped
    changed = any([unlink(directory / name) for name in QUADLET_FILES]) or changed
    runtime = directory / settings.KUBE_RUNTIME
    if runtime.is_symlink():
        runtime.unlink()
        changed = True
    elif runtime.exists():
        shutil.rmtree(runtime)
        changed = True
    systemctl('daemon-reload')
    # Direct kube play has no systemd owner to remove its pods/infra containers.
    # Pod removal does not request volume deletion; PVCs follow remove_data below.
    for name in PODS:
        changed = exists('pod', name) or changed
        run('podman', 'pod', 'rm', '--force', '--ignore', name)
    for name in CONTAINERS:
        changed = exists('container', name) or changed
        run('podman', 'rm', '--force', '--ignore', name)
    changed = remove('network', apps.NETWORK) or changed
    # The Kube secrets' volumes are copies of secrets, not data: they go now,
    # and the next install's kube play makes them again from the secrets.
    changed = secrets.remove_kube_volumes() or changed
    changed = unlink(settings.DEV_STATE_FILE) or changed
    if remove_data:
        for app in apps.APPS:
            changed = remove('volume', app.database.volume('data')) or changed
        changed = remove('volume', apps.KEYCLOAK_DATABASE.volume('data')) or changed
        for name in SECRETS:
            changed = remove('secret', name) or changed
        # nginx's CA and leaf are Podman secrets (in SECRETS above) or, with the TLS
        # volume, in todo-nginx-data; docs/ARCHITECTURE.md lists both with the
        # database volumes as surviving local app recreation, so they are only
        # removed alongside them, not on a plain uninstall.
        for name in TLS_VOLUMES:
            changed = remove('volume', name) or changed
        # The public hostnames go with the data they were installed for; a plain
        # uninstall keeps them, so a reinstall serves the same names.
        changed = unlink(target_render.record_path()) or changed
    for app in apps.APPS:
        for component in ('backend', 'frontend'):
            changed = remove('image', app.image(component)) or changed
    for reference in (apps.PROXY_IMAGE, apps.KEYCLOAK_IMAGE):
        changed = remove('image', reference) or changed
    return changed
