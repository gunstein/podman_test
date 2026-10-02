"""Single-host orchestration; shared workload functions also serve app-ops DR."""
from pathlib import Path

from . import apps, images, keycloak, quadlet, secrets, settings, target_render, workloads
from .commands import exists, run

LEGACY = tuple(app.names.resource(component) for app in apps.APPS
               for component in ('postgres', 'db-setup', 'migrate', 'db-grants', 'backend', 'frontend')) + (
                   'todo-keycloak', 'keycloak')


def services(applications):
    """Pod and service base names for the selected apps, including the shared ones."""
    return (*(app.pod for app in applications), 'keycloak',
            *(app.database.container for app in applications), 'keycloak-postgres', 'shared-proxy')


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


def require_single_host(action):
    """Refuse a host with replication, promotion or backup state; it is a DR node.

    The single-host installer and uninstaller do not know about DR. Rerunning
    install on a replicated primary rewrites its database units without the
    LAN publication, which silently cuts off the standby; uninstall would
    remove clustered state. A person has to decide what to do on such a host.
    """
    markers = (Path.home() / settings.DR_CONFIG / 'todo-standby-entrypoint.sh',
               settings.TOOLS_BIN / 'app_dr.py', settings.TOOLS_BIN / 'app_backup.py',
               # Names installed before the tools were renamed still mark a clustered host.
               settings.TOOLS_BIN / 'todo_dr.py', settings.TOOLS_BIN / 'todo_backup.py')
    if any(exists('secret', d.secret('replicator')) for d in apps.REPLICATED_DATABASES) or any(
            path.exists() for path in markers):
        raise RuntimeError(
            f'{action} only supports a single-host deployment. This host contains '
            'clustered replication, promotion or backup state. Preserve it and '
            'use the DR tools and runbooks instead (docs/ACCEPTANCE.md, deploy/dr).')


def setup_roles(app: apps.App = apps.APPS[0]):
    """Run the app's setup_roles.py once, in a throwaway container on app-network.

    It logs in as the database owner and creates the migrator and app roles
    with only the rights they need; see the backend's setup_roles.py.
    """
    argv = ['podman', 'run', '--rm', '--network', apps.NETWORK]
    roles = ['db', 'migrator', 'app']
    for role in roles:
        argv += ['--secret', app.database.secret(role)]
    for value in (f'DATABASE_HOST={app.database.container}',
                  f'DATABASE_NAME={app.name}', f'DATABASE_BOOTSTRAP_USER={app.name}'):
        argv += ['--env', value]
    run(*argv, '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
        app.image('backend'), 'python', '-m', 'backend.setup_roles')


def offline_files(applications):
    """The Kube YAML files and units an offline install of these apps writes, as two sets of names."""
    manifests = {'keycloak.yaml', apps.KEYCLOAK_DATABASE.manifest, apps.KEYCLOAK_DATABASE.config_manifest,
                 'shared-proxy.yaml', apps.SHARED_RESOURCE_OWNER.config_manifest}
    units = {apps.KEYCLOAK_DATABASE.unit, 'keycloak.kube', 'shared-proxy.kube'}
    for app in applications:
        manifests |= {app.database.manifest, app.config_manifest, app.manifest}
        units |= {app.database.unit, app.unit}
    return manifests, units


def load_target(bundle_directory, applications, publish_address, service_port, target_values):
    """Read and fill in the offline bundle's files, and check that they fit this install.

    Runs before anything on the host changes: a missing or invalid target
    value, an older bundle or a bundle for other apps or another port stops
    the install here.
    """
    target = target_render.load(bundle_directory, {**(target_values or {}),
                                                   target_render.PUBLISH_ADDRESS: publish_address},
                                recorded=target_render.read_record())
    if target.applications != tuple(app.name for app in applications):
        raise ValueError(f'The bundle was built for {", ".join(target.applications)}; '
                         'an offline install installs exactly those apps.')
    if service_port != target.public_port:
        raise ValueError(f'The bundle was built for HTTPS port {target.public_port}, not {service_port}.')
    manifests, units = offline_files(applications)
    missing = sorted((manifests - set(target.manifests)) | (units - set(target.quadlets)))
    if missing:
        raise ValueError('The bundle lacks ' + ', '.join(missing))
    return target


def app_hostnames(project_root, mode, target, applications):
    """Each app's public hostname: {app name: hostname}.

    From the offline bundle's target values, else from the environment's
    values.yaml and the app registry, which build and dev mode render with
    (render needs PyYAML, which those modes have).
    """
    if target is not None:
        return target.hostnames
    from . import render
    profile = 'local' if mode == 'dev' else 'prod'
    public = render.read_values(Path(project_root) / f'deploy/environments/{profile}/values.yaml')[0]
    return render.hostnames(applications, public)


def clients(applications, hostnames):
    """Each app's Keycloak client and the hostname it is served on."""
    return [(app.keycloak_client, hostnames[app.name]) for app in applications]


def _contents(path):
    """A file's bytes, or None if it does not exist."""
    return path.read_bytes() if path.exists() else None


def install(project_root, mode='server', deployment_mode='build', bundle_directory='',
            refresh_images=False, publish_address='127.0.0.1', service_port=settings.HTTPS_PORT,
            quadlet_dir=None, kube_runtime_dir=None, applications=None, target_values=None):
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

    An offline install takes the bundle's pre-rendered files and fills in the
    target values (target_values, from the command line; target_render says
    where else they may come from, the host's record among them) before
    anything changes, and records the hostnames once the install succeeded.
    It needs no Jinja2 or PyYAML, and installs in server mode only.

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
    target = None
    if deployment_mode == 'offline':
        if mode != 'server':
            raise ValueError('An offline bundle installs in server mode only.')
        target = load_target(bundle_directory, applications, publish_address, service_port, target_values)
    elif target_values and any(target_values.values()):
        raise ValueError('Target values only apply to an offline bundle (--deployment-mode offline).')
    require_single_host('install')
    root = Path(project_root).resolve()
    directory = Path(quadlet_dir or settings.QUADLET_DIR).resolve()
    runtime = Path(kube_runtime_dir or directory / settings.KUBE_RUNTIME).resolve()
    if mode == 'server' and runtime != directory / settings.KUBE_RUNTIME:
        raise ValueError(f'kube_runtime_dir must be quadlet_dir/{settings.KUBE_RUNTIME}')
    preflight(directory)
    run('podman', '--version')
    # An offline install takes its files from the bundle (target); the others render here.
    rendered = None if deployment_mode == 'offline' else root / 'generated' / ('dev' if mode == 'dev' else 'kube-runtime')
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
            secrets.create_kube(secrets.postgres_secret_mapping(app.database))
            secrets.create_kube(secrets.application_secret_mapping(app))
        secrets.create_kube(secrets.postgres_secret_mapping(apps.KEYCLOAK_DATABASE))
        secrets.create_kube(secrets.keycloak_secret_mapping())
        changed = up(rendered, applications, settings.DEV_STATE_FILE, images_changed)
        configured = keycloak.configure(
            secrets.read(apps.KEYCLOAK_ADMIN_SECRET),
            clients(applications, app_hostnames(root, mode, target, applications)))
        return changed or configured
    arguments = (root, directory, runtime, rendered)
    changed = False
    # postgres is the one image shared by every database (images.shared_images), so a
    # postgres image change restarts every postgres service; backend/frontend images are
    # per-app and only ever restart that app's own service. This keeps an unrelated app's
    # (or component's) update from taking down the whole stack.
    postgres_image_changed = shared_images['postgres']
    # An app's ConfigMap file (OIDC issuer, log level) is shared with its database
    # pod, whose install writes it first; install_application then finds it
    # unchanged. So compare it with what was installed before this run.
    configs_before = {app.name: _contents(runtime / app.config_manifest) for app in applications}
    restart = set()
    for app in applications:
        postgres_changed = workloads.install_postgres(*arguments, database=app.database, target=target)
        changed = postgres_changed or changed
        if postgres_changed or postgres_image_changed:
            restart.add(app.database.container)
        application_changed = workloads.install_application(
            *arguments, publish_address, service_port, app=app, target=target)
        changed = application_changed or changed
        config_changed = _contents(runtime / app.config_manifest) != configs_before[app.name]
        if (application_changed or config_changed or image_changes[app.name]['backend']
                or image_changes[app.name]['frontend']):
            restart.add(app.pod)
    keycloak_database_changed = workloads.install_postgres(*arguments, database=apps.KEYCLOAK_DATABASE,
                                                           target=target)
    changed = keycloak_database_changed or changed
    if keycloak_database_changed or postgres_image_changed:
        restart.add(apps.KEYCLOAK_DATABASE.container)
    keycloak_changed = workloads.install_keycloak(*arguments, target=target)
    changed = keycloak_changed or changed
    if keycloak_changed or shared_images['keycloak']:
        restart.add('keycloak')
    proxy_changed = workloads.install_shared_proxy(
        *arguments, publish_address, service_port, applications=applications, target=target)
    changed = proxy_changed or changed
    if proxy_changed or shared_images['proxy']:
        restart.add('shared-proxy')
    selected_services = services(applications)
    for service in selected_services:
        if service in restart:
            quadlet.systemctl('stop', service + '.service')
    for app in applications:
        quadlet.systemctl('start', app.database.service)
        run('podman', 'wait', '--condition=healthy', app.database.container,
            timeout=settings.HEALTH_TIMEOUT)
        setup_roles(app)
    quadlet.systemctl('start', apps.KEYCLOAK_DATABASE.service)
    run('podman', 'wait', '--condition=healthy', apps.KEYCLOAK_DATABASE.container,
        timeout=settings.HEALTH_TIMEOUT)
    quadlet.systemctl('start', 'keycloak.service')
    for app in applications:
        quadlet.systemctl('start', app.service)
        setup_roles(app)
    quadlet.systemctl('start', 'shared-proxy.service')
    configured = keycloak.configure(
        secrets.read(apps.KEYCLOAK_ADMIN_SECRET),
        clients(applications, app_hostnames(root, mode, target, applications)))
    for service in selected_services:
        source = quadlet.systemctl('show', service + '.service', '--property=SourcePath',
                                  '--value').stdout.strip()
        if source != str(runtime / (service + '.kube')):
            raise RuntimeError(f'Unexpected SourcePath for {service}: {source}')
    # The hostnames this host now serves, for the next install and the DR tools.
    if target is not None:
        target_render.write_record(target.values)
    return changed or images_changed or configured
