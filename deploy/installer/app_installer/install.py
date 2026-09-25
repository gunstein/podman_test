"""Single-host orchestration; shared workload functions also serve Ansible DR."""
from pathlib import Path

from . import apps, images, keycloak, quadlet, secrets, settings, workloads
from .commands import run

LEGACY = tuple(app.resource(component) for app in apps.APPS
               for component in ('postgres', 'db-setup', 'migrate', 'db-grants', 'backend', 'frontend')) + (
                   'todo-keycloak', 'keycloak')


def services(applications):
    """Pod and service base names for the selected apps, including the shared ones."""
    return (*(app.resource('app') for app in applications), 'keycloak',
            *(app.resource('postgres') for app in applications), 'keycloak-postgres', 'shared-proxy')


SERVICES = services(apps.APPS)


def preflight(quadlet_dir):
    """Refuse a host this installer does not support, before anything changes.

    Requires podman kube play --no-pod-prefix, which keeps container names
    stable, and refuses hosts that still run the old per-container Quadlet
    setup: that needs a person to review it, not an automatic migration.
    """
    if '--no-pod-prefix' not in run('podman', 'kube', 'play', '--help').stdout:
        raise RuntimeError('The final runtime requires the tested Podman --no-pod-prefix option.')
    if any((Path(quadlet_dir) / (name + '.container')).exists() for name in LEGACY):
        raise RuntimeError(
            'Unsupported per-container Quadlets are installed. Stop and review the host '
            'separately; clean deploy requires a Kube-compatible baseline and does not '
            'migrate or remove existing runtime state.')


def setup_roles(app: apps.App = apps.APPS[0]):
    """Run the app's setup_roles.py once, in a throwaway container on app-network.

    It logs in as the database owner and creates the migrator and app roles
    with only the rights they need; see the backend's setup_roles.py.
    """
    argv = ['podman', 'run', '--rm', '--network', apps.NETWORK]
    roles = ['db', 'migrator', 'app']
    for role in roles:
        argv += ['--secret', app.secret(role)]
    for value in (f'DATABASE_HOST={app.resource("postgres")}',
                  f'DATABASE_NAME={app.name}', f'DATABASE_BOOTSTRAP_USER={app.name}'):
        argv += ['--env', value]
    run(*argv, '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
        app.image('backend'), 'python', '-m', 'backend.setup_roles')


def install(project_root, mode='server', deployment_mode='build', bundle_directory='',
            refresh_images=False, publish_address='127.0.0.1', service_port=settings.HTTPS_PORT,
            quadlet_dir=None, kube_runtime_dir=None, applications=None):
    """Install or update the whole single-host stack. Safe to run again.

    Steps: check the host, render the Kube YAML (build mode) or use the
    bundle's (offline mode), create missing passwords, build or load
    images, and write the Quadlet units. In server mode, only services
    whose definition or image changed are restarted, then everything is
    started in dependency order. Roles are set up once the database is
    healthy, and again after the app starts, so the tables its migrations
    created get their grants. Finally Keycloak is configured and every unit
    is checked to run from the expected Kube file. mode='dev' runs the same
    YAML with podman kube play directly, without systemd.

    Returns True if anything changed.
    """
    applications = apps.APPS if applications is None else tuple(applications)
    if apps.SHARED_RESOURCE_OWNER not in applications:
        raise ValueError('The application that owns the shared resources must be included.')
    if mode not in ('dev', 'server'):
        raise ValueError('mode must be dev or server')
    if deployment_mode not in ('build', 'offline') or (
        deployment_mode == 'offline' and (not bundle_directory or refresh_images)
    ):
        raise ValueError('Offline deployment requires bundle_directory and forbids refresh_images.')
    root = Path(project_root).resolve()
    directory = Path(quadlet_dir or settings.QUADLET_DIR).resolve()
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
        secrets.create_kube(secrets.postgres_secret_mapping(apps.KEYCLOAK_DATABASE))
        secrets.create_kube(secrets.keycloak_secret_mapping())
        changed = up(rendered, applications, settings.DEV_STATE_FILE, images_changed)
        configured = keycloak.configure(
            secrets.read(apps.KEYCLOAK_ADMIN_SECRET),
            [(app.keycloak_client, app.hostname) for app in applications])
        return changed or configured
    arguments = (root, directory, runtime, rendered)
    changed = False
    # postgres is the one image shared by every database (settings.POSTGRES_IMAGE), so a
    # postgres image change restarts every postgres service; backend/frontend images are
    # per-app and only ever restart that app's own service. This keeps an unrelated app's
    # (or component's) update from taking down the whole stack.
    postgres_image_changed = any(image_changes[app.name]['postgres'] for app in applications)
    restart = set()
    for app in applications:
        postgres_changed = workloads.install_postgres(*arguments, app=app)
        changed = postgres_changed or changed
        if postgres_changed or postgres_image_changed:
            restart.add(app.resource('postgres'))
        application_changed = workloads.install_application(
            *arguments, publish_address, service_port, app=app)
        changed = application_changed or changed
        if application_changed or image_changes[app.name]['backend'] or image_changes[app.name]['frontend']:
            restart.add(app.resource('app'))
    keycloak_database_changed = workloads.install_postgres(*arguments, app=apps.KEYCLOAK_DATABASE)
    changed = keycloak_database_changed or changed
    if keycloak_database_changed or postgres_image_changed:
        restart.add(apps.KEYCLOAK_DATABASE.resource('postgres'))
    keycloak_changed = workloads.install_keycloak(*arguments)
    changed = keycloak_changed or changed
    if keycloak_changed or shared_images['keycloak']:
        restart.add('keycloak')
    proxy_changed = workloads.install_shared_proxy(
        *arguments, publish_address, service_port, applications=applications)
    changed = proxy_changed or changed
    if proxy_changed or shared_images['proxy']:
        restart.add('shared-proxy')
    selected_services = services(applications)
    for service in selected_services:
        if service in restart:
            quadlet.systemctl('stop', service + '.service')
    for app in applications:
        quadlet.systemctl('start', app.service('postgres'))
        run('podman', 'wait', '--condition=healthy', app.resource('postgres'))
        setup_roles(app)
    quadlet.systemctl('start', apps.KEYCLOAK_DATABASE.service('postgres'))
    run('podman', 'wait', '--condition=healthy', apps.KEYCLOAK_DATABASE.resource('postgres'))
    quadlet.systemctl('start', 'keycloak.service')
    for app in applications:
        quadlet.systemctl('start', app.service('app'))
        setup_roles(app)
    quadlet.systemctl('start', 'shared-proxy.service')
    configured = keycloak.configure(
        secrets.read(apps.KEYCLOAK_ADMIN_SECRET),
        [(app.keycloak_client, app.hostname) for app in applications])
    for service in selected_services:
        source = quadlet.systemctl('show', service + '.service', '--property=SourcePath',
                                  '--value').stdout.strip()
        if source != str(runtime / (service + '.kube')):
            raise RuntimeError(f'Unexpected SourcePath for {service}: {source}')
    return changed or images_changed or configured
