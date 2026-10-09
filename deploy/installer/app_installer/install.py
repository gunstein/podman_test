"""Single-host orchestration; shared workload functions also serve app-ops DR."""
from pathlib import Path

from . import (
    apps,
    backup,
    images,
    keycloak,
    quadlet,
    secrets,
    settings,
    target_render,
    tls_secrets,
    tls_store,
    workloads,
)
from .commands import exists, run

LEGACY = tuple(app.names.resource(component) for app in apps.APPS
               for component in ('postgres', 'db-setup', 'migrate', 'db-grants', 'backend', 'frontend')) + (
                   'todo-keycloak', 'keycloak')


def preflight(quadlet_dir):
    """Refuse a host this installer does not support, before anything changes.

    Requires podman kube play --no-pod-prefix, which keeps container names
    stable, and refuses hosts that still run the old per-container Quadlet
    setup: that needs a person to review it, not an automatic migration.
    """
    if '--no-pod-prefix' not in run('podman', 'kube', 'play', '--help').stdout:
        raise RuntimeError('This installer requires the Podman kube play --no-pod-prefix option.')
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
    selected = apps.workloads(applications)
    return {name for workload in selected for name in workload.manifests}, {workload.unit for workload in selected}


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


def check(project_root, mode, deployment_mode, bundle_directory, refresh_images, publish_address,
          service_port, quadlet_dir, kube_runtime_dir, applications, target_values):
    """Everything install checks before anything changes; return (root, quadlet dir, runtime dir, target).

    The arguments must fit together, an offline bundle must load with its
    target values and fit these apps and port, the host must not be a DR
    node or carry the old per-container units, and Podman must answer.
    target is the bundle's files filled in, or None in build mode.
    """
    if apps.IDENTITY_APP not in applications:
        raise ValueError(f'The identity app ({apps.IDENTITY_APP.name}) must be included: '
                         'Keycloak and every login use its hostname.')
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
    return root, directory, runtime, target


def prepare(root, mode, deployment_mode, bundle_directory, refresh_images, applications, target):
    """Render (build mode), passwords, images and nginx's TLS secrets; return what the next steps need.

    Returns (rendered, image_changes, shared_images, hostnames, tls_changed):
    the directory of the rendered Kube YAML (None offline), each app's and
    the shared images' changes from images.prepare, each app's public
    hostname, and whether nginx's TLS secret changed.
    """
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
    hostnames = app_hostnames(root, mode, target, applications)
    # nginx's TLS files as Podman secrets, before nginx starts (tls_secrets);
    # with the TLS volume, the shared-proxy pod's init container makes them.
    tls_changed = tls_store.secret_storage() and tls_secrets.provision(tls_secrets.ordered(hostnames))
    return rendered, image_changes, shared_images, hostnames, tls_changed


def write_definitions(root, directory, runtime, rendered, applications, publish_address, service_port,
                      target, image_changes, shared_images, tls_changed):
    """Write every workload's Kube YAML and unit; return (changed, the pods that must restart).

    A pod restarts when its definition, its ConfigMap, one of its images or,
    for nginx, its TLS secret changed.
    """
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
    if proxy_changed or shared_images['proxy'] or tls_changed:
        restart.add('shared-proxy')
    return changed, restart


def start_in_order(applications, restart):
    """Stop the pods in restart, last-started first, then start every workload in order.

    Roles are set up once an app's database is healthy, and again after the
    app started, so the tables its migrations created get their grants.
    """
    selected = apps.workloads(applications)
    for workload in reversed(selected):
        if workload.pod in restart:
            quadlet.systemctl('stop', workload.service)
    roles = {pod: app for app in applications for pod in (app.database.container, app.pod)}
    for workload in selected:
        quadlet.systemctl('start', workload.service)
        if workload.wait_healthy:
            run('podman', 'wait', '--condition=healthy', workload.pod, timeout=settings.HEALTH_TIMEOUT)
        if workload.pod in roles:
            setup_roles(roles[workload.pod])


def finish(applications, runtime, target, hostnames):
    """Configure Keycloak, check every unit's SourcePath, record the hostnames, turn the backup on.

    Returns (Keycloak changed, backup timer changed).
    """
    configured = keycloak.configure(secrets.read(apps.KEYCLOAK_ADMIN_SECRET), clients(applications, hostnames))
    for workload in apps.workloads(applications):
        source = quadlet.systemctl('show', workload.service, '--property=SourcePath', '--value').stdout.strip()
        if source != str(runtime / workload.unit):
            raise RuntimeError(f'Unexpected SourcePath for {workload.pod}: {source}')
    # The hostnames this host now serves, for the next install, tls.py and the DR tools.
    target_render.write_record(target.values if target is not None else
                               {target_render.hostname_target(app): hostnames[app.name] for app in applications})
    # Every server install backs itself up every night, so a single host can at
    # least go back to last night (backup.py).
    backups_changed = backup.install_timer(Path(__file__).resolve().parents[1])
    return configured, backups_changed


def install(project_root, mode='server', deployment_mode='build', bundle_directory='',
            refresh_images=False, publish_address='127.0.0.1', service_port=settings.HTTPS_PORT,
            quadlet_dir=None, kube_runtime_dir=None, applications=None, target_values=None):
    """Install or update the whole single-host stack. Safe to run again.

    Steps: check the host, render the Kube YAML (build mode) or use the
    bundle's (offline mode), create missing passwords, build or load
    images, give nginx its TLS files as Podman secrets (tls_secrets, when
    settings.NGINX_TLS_STORAGE is "secret"), and write the Quadlet units.
    In server mode, only services whose definition, image or TLS secret
    changed are restarted, then everything is
    started in dependency order. Roles are set up once the database is
    healthy, and again after the app starts, so the tables its migrations
    created get their grants. Finally Keycloak is configured and every unit
    is checked to run from the expected Kube file, and the nightly backup timer
    is turned on. mode='dev' renders with the local values and runs the YAML
    with podman kube play directly, without systemd.

    An offline install takes the bundle's pre-rendered files and fills in the
    target values (target_values, from the command line; target_render says
    where else they may come from, the host's record among them) before
    anything changes, and records the hostnames once the install succeeded.
    It needs no Jinja2 or PyYAML, and installs in server mode only.

    The steps are functions of their own, in this order: check, prepare,
    write_definitions, start_in_order and finish.

    Returns True if anything changed.
    """
    applications = apps.APPS if applications is None else tuple(applications)
    root, directory, runtime, target = check(
        project_root, mode, deployment_mode, bundle_directory, refresh_images, publish_address,
        service_port, quadlet_dir, kube_runtime_dir, applications, target_values)
    rendered, image_changes, shared_images, hostnames, tls_changed = prepare(
        root, mode, deployment_mode, bundle_directory, refresh_images, applications, target)
    images_changed = any(shared_images.values()) or any(
        any(changes.values()) for changes in image_changes.values())
    if mode == 'dev':
        from .kube_play import up
        for app in applications:
            secrets.create_kube(secrets.postgres_secret_mapping(app.database))
            secrets.create_kube(secrets.application_secret_mapping(app))
        secrets.create_kube(secrets.postgres_secret_mapping(apps.KEYCLOAK_DATABASE))
        secrets.create_kube(secrets.keycloak_secret_mapping())
        changed = up(rendered, applications, settings.DEV_STATE_FILE, images_changed or tls_changed)
        configured = keycloak.configure(secrets.read(apps.KEYCLOAK_ADMIN_SECRET), clients(applications, hostnames))
        return changed or configured or tls_changed
    changed, restart = write_definitions(root, directory, runtime, rendered, applications, publish_address,
                                         service_port, target, image_changes, shared_images, tls_changed)
    start_in_order(applications, restart)
    configured, backups_changed = finish(applications, runtime, target, hostnames)
    return changed or images_changed or tls_changed or configured or backups_changed
