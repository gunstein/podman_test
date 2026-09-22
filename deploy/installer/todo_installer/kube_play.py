"""Direct developer lifecycle. No systemd units and no forced volume removal."""
from pathlib import Path

from .commands import exists, run
from .install import setup_roles


def up(rendered_manifest_dir):
    directory = Path(rendered_manifest_dir)
    if not exists('network', 'todo-network'):
        run('podman', 'network', 'create', 'todo-network')
    for name in ('postgres', 'keycloak', 'app', 'shared-proxy'):
        ports = (['--publish', '127.0.0.1:8080:8080', '--publish', '127.0.0.1:8443:8443']
                 if name == 'shared-proxy' else [])
        run('podman', 'kube', 'play', '--no-pod-prefix', '--network', 'todo-network',
            '--configmap', directory / 'config.yaml', *ports, directory / (name + '.yaml'))
        if name == 'postgres':
            run('podman', 'wait', '--condition', 'healthy', 'todo-postgres')
            setup_roles()
    setup_roles()


def down(rendered_manifest_dir):
    directory = Path(rendered_manifest_dir)
    for name in ('shared-proxy', 'app', 'keycloak', 'postgres'):
        manifest = directory / (name + '.yaml')
        if manifest.is_file():
            run('podman', 'kube', 'play', '--down', manifest)
