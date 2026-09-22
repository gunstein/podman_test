"""Single-host uninstall, retaining DR refusal and opt-in database removal."""
import shutil
from pathlib import Path

from .commands import exists, run
from .install import LEGACY
from .quadlet import systemctl

QUADLET_FILES = ('app-network.network', 'todo-postgres-data.volume', 'todo-postgres-backup.volume',
                 'todo-nginx-data.volume', 'todo-caddy-data.volume',
                 *(name + '.container' for name in LEGACY))
SERVICES = ('todo-app', 'todo-frontend', 'todo-backend', 'keycloak', 'shared-proxy',
            'todo-db-grants', 'todo-migrate', 'todo-db-setup', 'todo-postgres', 'app-network',
            'todo-postgres-data-volume', 'todo-postgres-backup-volume', 'todo-nginx-data-volume')
CONTAINERS = ('todo-frontend', 'nginx', 'todo-backend', 'keycloak', 'todo-migrate',
              'todo-db-grants', 'todo-db-setup', 'todo-postgres')
SECRETS = ('todo-db-password', 'todo-migrator-password', 'todo-app-password',
           'todo-keycloak-db-password', 'todo-kube-postgres-secret', 'todo-kube-migrator-secret',
           'todo-kube-backend-secret', 'todo-kube-keycloak-secret', 'todo-keycloak-admin-password')


def remove(kind, name):
    if exists(kind, name):
        run('podman', kind, 'rm', name)


def uninstall(remove_data=False, quadlet_dir=None):
    directory = Path(quadlet_dir or Path.home() / '.config/containers/systemd')
    markers = (Path.home() / '.config/todo/todo-standby-entrypoint.sh',
               Path('/opt/todo/bin/todo_dr.py'), Path('/opt/todo/bin/todo_backup.py'))
    if exists('secret', 'todo-replicator-password') or any(path.exists() for path in markers):
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
    remove('network', 'app-network')
    if remove_data:
        remove('volume', 'todo-postgres-data')
        for name in SECRETS:
            remove('secret', name)
    for name in ('backend', 'frontend', 'proxy', 'keycloak'):
        remove('image', 'localhost/keycloak:m12' if name == 'keycloak'
               else f'localhost/todo-{name}:m12')
    for name in ('todo-nginx-data', 'todo-caddy-data'):
        remove('volume', name)
