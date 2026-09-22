"""Single-host uninstall, retaining DR refusal and opt-in database removal."""
import shutil
from pathlib import Path

from . import apps, secrets
from .commands import exists, run
from .install import LEGACY
from .quadlet import systemctl

IDENTITY = apps.IDENTITY_DATABASE_APP
TLS_VOLUMES = (IDENTITY.resource('nginx-data'), IDENTITY.resource('caddy-data'))
QUADLET_FILES = (apps.NETWORK + '.network',
                 *(app.volume(purpose) + '.volume' for app in apps.APPS
                   for purpose in ('data', 'backup')),
                 *(volume + '.volume' for volume in TLS_VOLUMES),
                 *(name + '.container' for name in LEGACY))
SERVICES = (*(app.resource(component) for app in apps.APPS
              for component in ('app', 'frontend', 'backend', 'db-grants',
                                'migrate', 'db-setup', 'postgres')),
            'keycloak', 'shared-proxy', apps.NETWORK + '-network',
            *(app.legacy_volume_service(purpose) for app in apps.APPS for purpose in ('data', 'backup')),
            IDENTITY.resource('nginx-data-volume'))
CONTAINERS = (*(app.resource(component) for app in apps.APPS
                for component in ('frontend', 'backend', 'migrate', 'db-grants', 'db-setup', 'postgres')),
              'nginx', 'keycloak')
MAPPINGS = {name: fields for app in apps.APPS
            for name, fields in {**secrets.postgres_secret_mapping(app),
                                 **secrets.application_secret_mapping(app)}.items()}
MAPPINGS.update(secrets.keycloak_secret_mapping())
SECRETS = tuple(dict.fromkeys([*MAPPINGS, *(source for fields in MAPPINGS.values()
                                          for source in fields.values())]))


def remove(kind, name):
    if exists(kind, name):
        run('podman', kind, 'rm', name)


def uninstall(remove_data=False, quadlet_dir=None):
    directory = Path(quadlet_dir or Path.home() / '.config/containers/systemd')
    markers = (Path.home() / '.config/todo/todo-standby-entrypoint.sh',
               Path('/opt/todo/bin/todo_dr.py'), Path('/opt/todo/bin/todo_backup.py'))
    if any(exists('secret', app.secret('replicator')) for app in apps.APPS) or any(
            path.exists() for path in markers):
        raise RuntimeError(
            'uninstall.yml only supports a single-host deployment. This host contains '
            'clustered replication, promotion or backup state. Preserve it and '
            'review the recovery inventory and operational runbooks separately.')
    run('systemctl', '--user', 'stop', *(name + '.service' for name in SERVICES), allowed=(0, 5))
    for name in QUADLET_FILES:
        (directory / name).unlink(missing_ok=True)
    runtime = directory / 'todo-kube-runtime'
    if runtime.is_symlink():
        runtime.unlink()
    elif runtime.exists():
        shutil.rmtree(runtime)
    systemctl('daemon-reload')
    for name in CONTAINERS:
        run('podman', 'rm', '--force', '--ignore', name)
    remove('network', apps.NETWORK)
    if remove_data:
        for app in apps.APPS:
            remove('volume', app.volume('data'))
        for name in SECRETS:
            remove('secret', name)
    for app in apps.APPS:
        for component in ('backend', 'frontend'):
            remove('image', app.image(component))
    for reference in (apps.PROXY_IMAGE, apps.KEYCLOAK_IMAGE):
        remove('image', reference)
    for name in TLS_VOLUMES:
        remove('volume', name)
