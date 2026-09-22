"""Single-host orchestration; shared workload functions also serve Ansible DR."""
from pathlib import Path

from . import images, keycloak, quadlet, secrets, workloads
from .commands import run

LEGACY = ('todo-postgres', 'todo-db-setup', 'todo-migrate', 'todo-db-grants',
          'todo-backend', 'todo-keycloak', 'todo-frontend')
SERVICES = ('todo-app', 'todo-keycloak', 'todo-postgres', 'shared-proxy')


def preflight(quadlet_dir):
    if '--no-pod-prefix' not in run('podman', 'kube', 'play', '--help').stdout:
        raise RuntimeError('The final runtime requires the tested Podman --no-pod-prefix option.')
    if any((Path(quadlet_dir) / (name + '.container')).exists() for name in LEGACY):
        raise RuntimeError(
            'Unsupported per-container Quadlets are installed. Stop and review the host '
            'separately; clean deploy requires a Kube-compatible baseline and does not '
            'migrate or remove existing runtime state.')


def setup_roles():
    argv = ['podman', 'run', '--rm', '--network', 'todo-network']
    for name in ('todo-db-password', 'todo-migrator-password', 'todo-app-password',
                 'todo-keycloak-db-password'):
        argv += ['--secret', name]
    for value in ('DATABASE_HOST=todo-postgres', 'DATABASE_NAME=todo', 'DATABASE_BOOTSTRAP_USER=todo'):
        argv += ['--env', value]
    run(*argv, '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
        'localhost/todo-backend:m12', 'python', '-m', 'backend.setup_roles')


def install(project_root, mode='server', deployment_mode='build', bundle_directory='',
            refresh_images=False, publish_address='127.0.0.1', service_port=8443,
            quadlet_dir=None, kube_runtime_dir=None):
    if mode not in ('dev', 'server'):
        raise ValueError('mode must be dev or server')
    if deployment_mode not in ('build', 'offline') or (
        deployment_mode == 'offline' and (not bundle_directory or refresh_images)
    ):
        raise ValueError('Offline deployment requires bundle_directory and forbids refresh_images.')
    root = Path(project_root).resolve()
    directory = Path(quadlet_dir or Path.home() / '.config/containers/systemd').resolve()
    runtime = Path(kube_runtime_dir or directory / 'todo-kube-runtime').resolve()
    if mode == 'server' and runtime != directory / 'todo-kube-runtime':
        raise ValueError('kube_runtime_dir must be quadlet_dir/todo-kube-runtime')
    preflight(directory)
    run('podman', '--version')
    rendered = (Path(bundle_directory) / 'generated/kube-runtime' if deployment_mode == 'offline'
                else root / 'generated' / ('dev' if mode == 'dev' else 'kube-runtime'))
    if deployment_mode == 'build':
        profile = 'local' if mode == 'dev' else 'prod'
        run(root / 'deploy/scripts/render-kube-runtime.sh',
            root / f'deploy/environments/{profile}/values.yaml', rendered)
    secrets.provision()
    image_changes = images.prepare(root, deployment_mode, bundle_directory, refresh_images)
    if mode == 'dev':
        from .kube_play import up
        secrets.create_kube(secrets.POSTGRES)
        secrets.create_kube(secrets.APPLICATION)
        up(rendered)
        return
    arguments = (root, directory, runtime, rendered)
    changed = workloads.install_postgres(*arguments)
    changed = workloads.install_application(*arguments, publish_address, service_port) or changed
    changed = workloads.install_shared_proxy(*arguments, publish_address, service_port) or changed
    if changed or image_changes['proxy']:
        for service in SERVICES:
            quadlet.systemctl('stop', service + '.service')
    quadlet.systemctl('start', 'todo-postgres.service')
    run('podman', 'wait', '--condition=healthy', 'todo-postgres')
    setup_roles()
    quadlet.systemctl('start', 'todo-app.service')
    setup_roles()
    quadlet.systemctl('start', 'shared-proxy.service')
    keycloak.configure(secrets.read('todo-keycloak-admin-password'))
    for service in SERVICES:
        source = quadlet.systemctl('show', service + '.service', '--property=SourcePath',
                                  '--value').stdout.strip()
        if source != str(runtime / (service + '.kube')):
            raise RuntimeError(f'Unexpected SourcePath for {service}: {source}')
