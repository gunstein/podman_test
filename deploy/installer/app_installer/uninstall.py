"""Single-host uninstall, retaining DR refusal and opt-in database removal."""
import shutil
from pathlib import Path

from . import apps, install, secrets, settings
from .commands import exists, run
from .quadlet import systemctl

SHARED = apps.SHARED_RESOURCE_OWNER
# caddy-data is a retired Caddy-based proxy's volume name; kept here so a host
# still carrying it from before the nginx migration gets it cleaned up too.
TLS_VOLUMES = (SHARED.resource('nginx-data'), SHARED.resource('caddy-data'))
QUADLET_FILES = (apps.NETWORK + '.network',
                 *(app.volume(purpose) + '.volume' for app in apps.APPS
                   for purpose in ('data', 'backup')),
                 *(apps.KEYCLOAK_DATABASE.volume(purpose) + '.volume' for purpose in ('data', 'backup')),
                 *(volume + '.volume' for volume in TLS_VOLUMES))
SERVICES = (*(app.resource(component) for app in apps.APPS
              for component in ('app', 'frontend', 'backend', 'db-grants',
                                'migrate', 'db-setup', 'postgres')),
            'keycloak', apps.KEYCLOAK_DATABASE.resource('postgres'), 'shared-proxy', apps.NETWORK + '-network')
CONTAINERS = (*(app.resource(component) for app in apps.APPS
                for component in ('frontend', 'backend', 'migrate', 'db-grants', 'db-setup', 'postgres')),
              'nginx', 'keycloak')
PODS = ('shared-proxy', *(app.resource('app') for app in reversed(apps.APPS)),
        'keycloak', apps.KEYCLOAK_DATABASE.resource('postgres'),
        *(app.resource('postgres') for app in reversed(apps.APPS)))
MAPPINGS = {name: fields for app in apps.APPS
            for name, fields in {**secrets.postgres_secret_mapping(app),
                                 **secrets.application_secret_mapping(app)}.items()}
MAPPINGS.update(secrets.postgres_secret_mapping(apps.KEYCLOAK_DATABASE))
MAPPINGS.update(secrets.keycloak_secret_mapping())
SECRETS = tuple(dict.fromkeys([*MAPPINGS, *(source for fields in MAPPINGS.values()
                                          for source in fields.values())]))


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
    a DR node and needs a person to decide. Database volumes, TLS volumes and
    secrets are kept unless remove_data is True, so reinstalling keeps the
    data and passwords. Returns True if anything was removed.
    """
    directory = Path(quadlet_dir or settings.QUADLET_DIR)
    install.require_single_host('uninstall')
    stopped = run('systemctl', '--user', 'stop', *(name + '.service' for name in SERVICES),
                  allowed=(0, 5)).returncode == 0
    changed = any([unlink(directory / name) for name in QUADLET_FILES]) or stopped
    runtime = directory / 'todo-kube-runtime'
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
    changed = unlink(settings.DEV_STATE_FILE) or changed
    if remove_data:
        for app in apps.APPS:
            changed = remove('volume', app.volume('data')) or changed
        changed = remove('volume', apps.KEYCLOAK_DATABASE.volume('data')) or changed
        for name in SECRETS:
            changed = remove('secret', name) or changed
        # todo-nginx-data holds the demo CA and leaf-key state; docs/ARCHITECTURE.md
        # lists it with the database volumes as surviving local app recreation, so
        # it is only removed alongside them, not on a plain uninstall.
        for name in TLS_VOLUMES:
            changed = remove('volume', name) or changed
    for app in apps.APPS:
        for component in ('backend', 'frontend'):
            changed = remove('image', app.image(component)) or changed
    for reference in (apps.PROXY_IMAGE, apps.KEYCLOAK_IMAGE):
        changed = remove('image', reference) or changed
    return changed
