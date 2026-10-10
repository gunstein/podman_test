"""Single-host orchestration; shared workload functions also serve app-ops DR."""
from pathlib import Path

from . import (
    apps,
    backup,
    checks,
    images,
    keycloak,
    platform_file,
    quadlet,
    secrets,
    settings,
    target_render,
    tls_secrets,
    tls_store,
    workloads,
)
from .commands import exists, run


def legacy_units(platform):
    """The per-container Quadlet units the retired implementation installed for these apps."""
    return tuple(app.names.resource(component) for app in platform.apps
                 for component in ('postgres', 'db-setup', 'migrate', 'db-grants', 'backend', 'frontend')) + (
                     'todo-keycloak', 'keycloak')


def preflight(quadlet_dir, platform):
    """Refuse a host this installer does not support, before anything changes.

    Requires podman kube play --no-pod-prefix, which keeps container names
    stable, and refuses hosts that still run the old per-container Quadlet
    setup: that needs a person to review it, not an automatic migration.
    """
    if '--no-pod-prefix' not in run('podman', 'kube', 'play', '--help').stdout:
        raise RuntimeError('This installer requires the Podman kube play --no-pod-prefix option.')
    if any((Path(quadlet_dir) / (name + '.container')).exists() for name in legacy_units(platform)):
        raise RuntimeError(
            'Unsupported per-container Quadlets are installed. Stop and review the host '
            'separately; clean deploy requires a Kube-compatible baseline and does not '
            'migrate or remove existing runtime state.')


def require_single_host(action, platform):
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
    if any(exists('secret', d.secret('replicator')) for d in platform.replicated_databases) or any(
            path.exists() for path in markers):
        raise RuntimeError(
            f'{action} only supports a single-host deployment. This host contains '
            'clustered replication, promotion or backup state. Preserve it and '
            'use the DR tools and runbooks instead (docs/ACCEPTANCE.md, deploy/dr).')


def setup_roles(app):
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


def offline_files(platform):
    """The Kube YAML files and units an offline install of this platform writes, as two sets of names."""
    selected = platform.workloads()
    return {name for workload in selected for name in workload.manifests}, {workload.unit for workload in selected}


def load_target(bundle_directory, platform, publish_address, service_port, target_values):
    """Read and fill in the offline bundle's files, and check that they fit this install.

    Runs before anything on the host changes: a missing or invalid target
    value, an older bundle, a bundle for another platform than the one asked
    for (platform, None for the bundle's own) or another port than
    service_port (None: the bundle's own) stops the install here.
    """
    target = target_render.load(bundle_directory, {**(target_values or {}),
                                                   target_render.PUBLISH_ADDRESS: publish_address},
                                recorded=target_render.read_record())
    if platform is not None and platform != target.platform:
        raise ValueError(f'The bundle was built for {", ".join(app.name for app in target.platform.apps)}; '
                         'an offline install installs exactly its own platform.')
    if service_port is not None and service_port != target.public_port:
        raise ValueError(f'The bundle was built for HTTPS port {target.public_port}, not {service_port}.')
    manifests, units = offline_files(target.platform)
    missing = sorted((manifests - set(target.manifests)) | (units - set(target.quadlets)))
    if missing:
        raise ValueError('The bundle lacks ' + ', '.join(missing))
    return target


def environment_name(mode):
    """The platform.yaml environment of a build or dev mode install: local for dev, prod for a server."""
    return 'local' if mode == 'dev' else 'prod'


def build_settings(project_root, mode, service_port):
    """(Platform, Environment) of a build or dev mode install, from project_root's platform.yaml.

    service_port, if given, must be the environment's publicPort: the
    rendered URLs and the published port come from the same number.
    Reading platform.yaml needs PyYAML, which those modes have.
    """
    platform, environment = platform_file.load(Path(project_root) / platform_file.FILE, environment_name(mode))
    if service_port is not None and service_port != environment.public_port:
        raise ValueError(f'platform.yaml gives the {environment_name(mode)} environment HTTPS port '
                         f'{environment.public_port}, not {service_port}: change publicPort there.')
    return platform, environment


def public_hostnames(platform, target):
    """Each app's public hostname and Keycloak's: ({app name: hostname}, Keycloak's hostname).

    From the offline bundle's target values, else the platform's defaults.
    """
    if target is not None:
        return target.hostnames, target.identity_hostname
    return {app.name: app.hostname for app in platform.apps}, platform.identity_hostname


def clients(platform, hostnames):
    """Each login app's Keycloak client and the hostname it is served on."""
    return [(app.keycloak_client, hostnames[app.name]) for app in platform.login_apps]


def configure_identity(platform, hostnames):
    """Configure Keycloak's clients (keycloak.configure) if it runs; True if Keycloak changed."""
    if not platform.has_identity:
        return False
    return keycloak.configure(secrets.read(apps.KEYCLOAK_ADMIN_SECRET), clients(platform, hostnames))


def _contents(path):
    """A file's bytes, or None if it does not exist."""
    return path.read_bytes() if path.exists() else None


def check(project_root, mode, deployment_mode, bundle_directory, refresh_images, publish_address,
          service_port, quadlet_dir, kube_runtime_dir, platform, target_values):
    """Everything install checks before anything changes.

    Returns (platform, environment, port, root, quadlet dir, runtime dir, target).
    The arguments must fit together, an offline bundle must load with its
    target values and fit this port (and platform, if one is given), the
    host must not be a DR node or carry the old per-container units, and
    Podman must answer. platform is the one to install: the bundle's, else
    the one given, else platform.yaml's. environment is platform.yaml's
    (platform_file.Environment), None offline; port is the HTTPS port, the
    environment's or the bundle's. target is the bundle's files filled in,
    or None in build mode.
    """
    if mode not in ('dev', 'server'):
        raise ValueError('mode must be dev or server')
    if deployment_mode not in ('build', 'offline') or (
        deployment_mode == 'offline' and (not bundle_directory or refresh_images)
    ):
        raise ValueError('Offline deployment requires bundle_directory and forbids refresh_images.')
    target = environment = None
    if deployment_mode == 'offline':
        if mode != 'server':
            raise ValueError('An offline bundle installs in server mode only.')
        target = load_target(bundle_directory, platform, publish_address, service_port, target_values)
    elif target_values and any(target_values.values()):
        raise ValueError('Target values only apply to an offline bundle (--deployment-mode offline).')
    root = Path(project_root).resolve()
    if target is None:
        configured, environment = build_settings(root, mode, service_port)
        platform, port = platform or configured, environment.public_port
        from . import render  # Jinja2 and PyYAML: build mode only
        render.require_supported(root, platform)
    else:
        platform, port = target.platform, target.public_port
    require_single_host('install', platform)
    directory = Path(quadlet_dir or settings.QUADLET_DIR).resolve()
    runtime = Path(kube_runtime_dir or directory / settings.KUBE_RUNTIME).resolve()
    if mode == 'server' and runtime != directory / settings.KUBE_RUNTIME:
        raise ValueError(f'kube_runtime_dir must be quadlet_dir/{settings.KUBE_RUNTIME}')
    preflight(directory, platform)
    run('podman', '--version')
    return platform, environment, port, root, directory, runtime, target


def prepare(root, mode, deployment_mode, bundle_directory, refresh_images, platform, environment, target):
    """Record the platform, render (build mode), passwords, images and nginx's TLS secrets.

    Returns (rendered, image_changes, shared_images, hostnames, identity, tls_changed):
    the directory of the rendered Kube YAML (None offline), each app's and
    the shared images' changes from images.prepare, each app's public
    hostname, Keycloak's, and whether nginx's TLS secret changed.
    """
    # An offline install takes its files from the bundle (target); the others render here, into the
    # checkout's generated/, which also checks each app's pod (pod_contract) before the host changes.
    rendered = None if deployment_mode == 'offline' else root / 'generated' / ('dev' if mode == 'dev' else 'kube-runtime')
    if deployment_mode == 'build':
        from . import render  # Jinja2 and PyYAML: build mode only
        render.render(root, environment, rendered, platform)
    # Before anything on the host changes: what this host may now hold, for uninstall and the DR tools.
    target_render.record_platform(platform)
    secrets.provision(platform)
    image_changes = {}
    for app in platform.apps:
        image_changes[app.name] = images.prepare(root, deployment_mode, bundle_directory, refresh_images, app=app)
    shared_images = images.prepare_shared(root, deployment_mode, bundle_directory, refresh_images, platform)
    hostnames, identity = public_hostnames(platform, target)
    # nginx's TLS files as Podman secrets, before nginx starts (tls_secrets);
    # with the TLS volume, the shared-proxy pod's init container makes them.
    tls_changed = tls_store.secret_storage() and tls_secrets.provision(tls_secrets.ordered(hostnames, identity))
    return rendered, image_changes, shared_images, hostnames, identity, tls_changed


def write_definitions(root, directory, runtime, rendered, platform, publish_address, service_port,
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
    postgres_image_changed = shared_images.get('postgres', False)
    # An app's ConfigMap file (OIDC issuer, log level) is shared with its database
    # pod, whose install writes it first; install_application then finds it
    # unchanged. So compare it with what was installed before this run.
    configs_before = {app.name: _contents(runtime / app.config_manifest) for app in platform.apps}
    restart = set()
    for app in platform.apps:
        if app.has_database:
            postgres_changed = workloads.install_postgres(*arguments, database=app.database, target=target)
            changed = postgres_changed or changed
            if postgres_changed or postgres_image_changed:
                restart.add(app.database.container)
        application_changed = workloads.install_application(*arguments, app=app, target=target)
        changed = application_changed or changed
        config_changed = _contents(runtime / app.config_manifest) != configs_before[app.name]
        if application_changed or config_changed or any(image_changes[app.name].values()):
            restart.add(app.pod)
    if platform.has_identity:
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
        *arguments, publish_address, service_port, platform=platform, target=target)
    changed = proxy_changed or changed
    if proxy_changed or shared_images['proxy'] or tls_changed:
        restart.add('shared-proxy')
    return changed, restart


def start_in_order(platform, restart):
    """Stop the pods in restart, last-started first, then start every workload in order.

    Roles are set up once an app's database is healthy, and again after the
    app started, so the tables its migrations created get their grants.
    """
    selected = platform.workloads()
    for workload in reversed(selected):
        if workload.pod in restart:
            quadlet.systemctl('stop', workload.service)
    roles = {pod: app for app in platform.database_apps for pod in (app.database.container, app.pod)}
    for workload in selected:
        quadlet.systemctl('start', workload.service)
        if workload.wait_healthy:
            run('podman', 'wait', '--condition=healthy', workload.pod, timeout=settings.HEALTH_TIMEOUT)
        if workload.pod in roles:
            setup_roles(roles[workload.pod])


def finish(platform, runtime, target, hostnames, identity):
    """Configure Keycloak (if it runs), check each app and every unit's SourcePath, record the hostnames,
    turn the backup on.

    Each app is checked (checks.verify: ready, then its checks) after
    Keycloak is set up, so identity setup never waits for an app. A check
    sends no token or cookie. Returns (Keycloak changed, backup timer changed).
    """
    configured = configure_identity(platform, hostnames)
    checks.verify(platform, hostnames)
    for workload in platform.workloads():
        source = quadlet.systemctl('show', workload.service, '--property=SourcePath', '--value').stdout.strip()
        if source != str(runtime / workload.unit):
            raise RuntimeError(f'Unexpected SourcePath for {workload.pod}: {source}')
    # The hostnames this host now serves, for the next install, tls.py and the DR tools.
    target_render.write_record(target.values if target is not None else
                               {target_render.IDENTITY_HOSTNAME: identity,
                                **{target_render.hostname_target(app): hostnames[app.name] for app in platform.apps}})
    # Every server install backs itself up every night, so a single host can at
    # least go back to last night (backup.py).
    backups_changed = backup.install_timer(Path(__file__).resolve().parents[1])
    return configured, backups_changed


def install(project_root, mode='server', deployment_mode='build', bundle_directory='',
            refresh_images=False, publish_address='127.0.0.1', service_port=None,
            quadlet_dir=None, kube_runtime_dir=None, platform=None, target_values=None):
    """Install or update the whole single-host stack. Safe to run again.

    platform is what to install: by default an offline bundle's own, or in
    build and dev mode platform.yaml's (build_settings). The host records it
    first (target_render.record_platform). The HTTPS port is platform.yaml's
    publicPort or the bundle's; service_port, if given, must be the same.
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
    is turned on. mode='dev' renders with platform.yaml's local environment and runs the YAML
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
    platform, environment, port, root, directory, runtime, target = check(
        project_root, mode, deployment_mode, bundle_directory, refresh_images, publish_address,
        service_port, quadlet_dir, kube_runtime_dir, platform, target_values)
    rendered, image_changes, shared_images, hostnames, identity, tls_changed = prepare(
        root, mode, deployment_mode, bundle_directory, refresh_images, platform, environment, target)
    images_changed = any(shared_images.values()) or any(
        any(changes.values()) for changes in image_changes.values())
    if mode == 'dev':
        from .kube_play import up
        secrets.create_kube(secrets.kube_mappings(platform))
        changed = up(rendered, platform, settings.DEV_STATE_FILE, images_changed or tls_changed)
        configured = configure_identity(platform, hostnames)
        checks.verify(platform, hostnames)
        return changed or configured or tls_changed
    changed, restart = write_definitions(root, directory, runtime, rendered, platform, publish_address,
                                         port, target, image_changes, shared_images, tls_changed)
    start_in_order(platform, restart)
    configured, backups_changed = finish(platform, runtime, target, hostnames, identity)
    return changed or images_changed or tls_changed or configured or backups_changed
