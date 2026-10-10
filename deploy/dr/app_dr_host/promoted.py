"""Deploy the application tier on the host whose databases were promoted as a group."""
import time
from pathlib import Path

from app_installer import (
    images,
    install,
    keycloak,
    preflight,
    quadlet,
    settings,
    target_render,
    workloads,
)
from app_installer.commands import exists, run

from . import nginx_tls, replication, transfer

CA_CERTIFICATE = '/var/lib/platform-tls/ca.crt'
DISCOVERY = '/auth/realms/todo/.well-known/openid-configuration'


def require_identity(inventory_hostname, node_address, service_port):
    """Raise unless this host has the inventory hostname and address, and the port is usable."""
    hostname = run('hostname').stdout.strip()
    addresses = preflight.ipv4_addresses(run('ip', '-4', '-o', 'address', 'show', 'scope', 'global').stdout)
    problems = []
    if hostname != inventory_hostname:
        problems.append(f'hostname {hostname!r} is not the inventory name {inventory_hostname!r}')
    if node_address not in addresses:
        problems.append(f'{node_address} is not a global IPv4 address on this host')
    if not 1024 <= service_port <= 65535:
        problems.append(f'service port {service_port} is outside 1024-65535')
    if problems:
        raise RuntimeError('Check the recovery inventory: ' + '; '.join(problems) + '.')


def install_workloads(project_root, quadlet_dir, target, node_address, service_port):
    """Install every app, Keycloak (if it runs) and the proxy from the bundle's files, published on this host's own address."""
    install.preflight(quadlet_dir, target.platform)
    runtime = quadlet_dir / settings.KUBE_RUNTIME
    arguments = (project_root, quadlet_dir, runtime, None)
    changed = False
    for app in target.platform.apps:
        changed = workloads.install_application(*arguments, app=app, target=target) or changed
    if target.platform.has_identity:
        changed = workloads.install_keycloak(*arguments, target=target) or changed
    return workloads.install_shared_proxy(*arguments, node_address, service_port,
                                          platform=target.platform, target=target) or changed


def require_application(app, hostname):
    """Health, database readiness and a public read, all through the app's nginx virtual host."""
    keycloak.wait('/health', 30, 1, 'ok', hostname=hostname)
    keycloak.wait('/ready', 30, 1, 'ready', hostname=hostname)
    if not isinstance(keycloak.request(app.api_path(), hostname=hostname), list):
        raise RuntimeError(f'{app.name}: public read {app.api_path()} did not return a list')


def require_issuer(issuer, attempts=90, delay=2):
    """Wait until Keycloak reports exactly the expected issuer URL, or raise."""
    seen = None
    for attempt in range(attempts):
        try:
            seen = keycloak.request(DISCOVERY).get('issuer')
            if seen == issuer:
                return
        except (OSError, ValueError, RuntimeError):
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise RuntimeError(f'Keycloak issuer {seen!r} did not become the canonical {issuer!r}')


def deploy(*, project_root, quadlet_dir, bundle_dir, inventory_hostname, node_address,
           journal, config_dir, service_port):
    """Start the application tier on this promoted host; return whether anything changed.

    In order, each step refusing before the next can run: check the host's
    identity and the bundle's port, require the promoted group record and
    every credential it needs, load missing images, require for a pair in
    provided mode this host's own certificate from the organisation's CA
    (nginx_tls), give nginx its TLS files as Podman secrets (a new demo CA
    in local mode unless this host has one), install the apps,
    Keycloak and nginx (stopping the tier first if anything changed), start
    them, wait for each app and the expected issuer, correct the Keycloak
    clients, record the hostnames, and save the nginx CA certificate as
    config_dir/platform-nginx-root.crt (the demo CA for the operator to hand to
    clients, or the organisation's root they already trust).

    The application files are the operations package's (project_root),
    filled in with this host's address and the public hostnames it recorded
    when it became a standby, so users reach the same names as before; the
    images come from the offline bundle (bundle_dir).
    """
    project_root, quadlet_dir, bundle_dir, config_dir = map(Path, (project_root, quadlet_dir, bundle_dir, config_dir))
    require_identity(inventory_hostname, node_address, service_port)
    target = target_render.load_on_host(project_root, node_address)
    if service_port != target.public_port:
        raise ValueError(f'The bundle was built for HTTPS port {target.public_port}, not {service_port}.')
    hostnames, platform = target.hostnames, target.platform
    replication.require_promoted_group(platform, journal)
    target_render.record_platform(platform)
    missing = [name for name in transfer.replicated_names(platform) if not exists('secret', name)]
    if missing:
        raise RuntimeError('Credentials required by the promoted group are missing: ' + ', '.join(missing))
    images_changed = images.prepare_offline_group(bundle_dir, platform)
    # A pair in provided mode never starts nginx here with a new demo CA. Checked with
    # the proxy image just loaded, before any workload changes.
    nginx_tls.require_for_failover()
    # nginx's TLS files as Podman secrets on this host, before nginx starts.
    tls_changed = nginx_tls.provision(hostnames, target.identity_hostname)
    workloads_changed = install_workloads(project_root, quadlet_dir, target, node_address, service_port)
    if images_changed or workloads_changed or tls_changed:
        run('systemctl', '--user', 'stop', *platform.services(databases=False), allowed=(0, 5))
    for workload in platform.serving_workloads():
        quadlet.systemctl('start', workload.service)
    for app in platform.apps:
        require_application(app, hostnames[app.name])
    if platform.has_identity:
        require_issuer(f'https://{target.identity_hostname}:{service_port}/auth/realms/todo')
    clients_changed = install.configure_identity(platform, hostnames)
    target_render.write_record(target.values)
    certificate = run('podman', 'exec', 'nginx', 'cat', CA_CERTIFICATE).stdout.strip() + '\n'
    certificate_changed = quadlet.write(config_dir / 'platform-nginx-root.crt', certificate.encode(), 0o644)
    return images_changed or workloads_changed or tls_changed or clients_changed or certificate_changed
