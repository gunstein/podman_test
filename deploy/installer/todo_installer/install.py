"""Single-host orchestration; shared workload functions also serve Ansible DR."""
from pathlib import Path

from . import apps, images, keycloak, quadlet, secrets, workloads
from .commands import run

LEGACY = tuple(app.resource(component) for app in apps.APPS
               for component in ('postgres', 'db-setup', 'migrate', 'db-grants', 'backend', 'frontend')) + (
                   'todo-keycloak', 'keycloak')


def services(applications):
    return (*(app.resource('app') for app in applications), 'keycloak',
            *(app.resource('postgres') for app in applications), 'shared-proxy')


SERVICES = services(apps.APPS)


def preflight(quadlet_dir):
    if '--no-pod-prefix' not in run('podman', 'kube', 'play', '--help').stdout:
        raise RuntimeError('The final runtime requires the tested Podman --no-pod-prefix option.')
    if any((Path(quadlet_dir) / (name + '.container')).exists() for name in LEGACY):
        raise RuntimeError(
            'Unsupported per-container Quadlets are installed. Stop and review the host '
            'separately; clean deploy requires a Kube-compatible baseline and does not '
            'migrate or remove existing runtime state.')


def setup_roles(app: apps.App = apps.APPS[0]):
    argv = ['podman', 'run', '--rm', '--network', apps.NETWORK]
    roles = ['db', 'migrator', 'app']
    if app == apps.IDENTITY_DATABASE_APP:
        roles.append('keycloak-db')
    for role in roles:
        argv += ['--secret', app.secret(role)]
    for value in (f'DATABASE_HOST={app.resource("postgres")}',
                  f'DATABASE_NAME={app.name}', f'DATABASE_BOOTSTRAP_USER={app.name}'):
        argv += ['--env', value]
    run(*argv, '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
        app.image('backend'), 'python', '-m', 'backend.setup_roles')


def install(project_root, mode='server', deployment_mode='build', bundle_directory='',
            refresh_images=False, publish_address='127.0.0.1', service_port=8443,
            quadlet_dir=None, kube_runtime_dir=None, applications=None):
    applications = apps.APPS if applications is None else tuple(applications)
    if apps.IDENTITY_DATABASE_APP not in applications:
        raise ValueError('The shared identity database application must be included.')
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
        selection = [','.join(app.name for app in applications)] if tuple(applications) != apps.APPS else []
        run(root / 'deploy/scripts/render-kube-runtime.sh',
            root / f'deploy/environments/{profile}/values.yaml', rendered, *selection)
    secrets.provision(applications)
    image_changes = {}
    for app in applications:
        image_changes[app.name] = images.prepare(
            root, deployment_mode, bundle_directory, refresh_images, app=app, include_shared=False)
    shared_images = images.prepare_shared(root, deployment_mode, bundle_directory, refresh_images)
    images_changed = any(shared_images.values()) or any(
        any(changes.values()) for changes in image_changes.values())
    if mode == 'dev':
        from .kube_play import up
        for app in applications:
            secrets.create_kube(secrets.postgres_secret_mapping(app))
            secrets.create_kube(secrets.application_secret_mapping(app))
        secrets.create_kube(secrets.keycloak_secret_mapping())
        changed = up(rendered, applications, directory.parent / 'todo-installer-dev.json', images_changed)
        configured = keycloak.configure(
            secrets.read(apps.IDENTITY_DATABASE_APP.secret('keycloak-admin')),
            [(app.keycloak_client, app.hostname) for app in applications])
        return changed or configured
    arguments = (root, directory, runtime, rendered)
    changed = False
    for app in applications:
        changed = workloads.install_postgres(*arguments, app=app) or changed
        changed = workloads.install_application(
            *arguments, publish_address, service_port, app=app) or changed
    changed = workloads.install_keycloak(*arguments) or changed
    changed = workloads.install_shared_proxy(
        *arguments, publish_address, service_port, applications=applications) or changed
    selected_services = services(applications)
    if changed or images_changed:
        for service in selected_services:
            quadlet.systemctl('stop', service + '.service')
    for app in applications:
        quadlet.systemctl('start', app.service('postgres'))
        run('podman', 'wait', '--condition=healthy', app.resource('postgres'))
        setup_roles(app)
    quadlet.systemctl('start', 'keycloak.service')
    for app in applications:
        quadlet.systemctl('start', app.service('app'))
        setup_roles(app)
    quadlet.systemctl('start', 'shared-proxy.service')
    configured = keycloak.configure(
        secrets.read(apps.IDENTITY_DATABASE_APP.secret('keycloak-admin')),
        [(app.keycloak_client, app.hostname) for app in applications])
    for service in selected_services:
        source = quadlet.systemctl('show', service + '.service', '--property=SourcePath',
                                  '--value').stdout.strip()
        if source != str(runtime / (service + '.kube')):
            raise RuntimeError(f'Unexpected SourcePath for {service}: {source}')
    return changed or images_changed or configured
