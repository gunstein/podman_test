"""Deploy the application tier on the host whose databases were promoted as a group."""
import time
from pathlib import Path

from app_installer import (
    apps,
    images,
    install,
    keycloak,
    preflight,
    quadlet,
    secrets,
    settings,
    target_render,
    workloads,
)
from app_installer.commands import exists, run

from . import replication, transfer

CA_CERTIFICATE = '/var/lib/todo-tls/ca.crt'
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
    """Install every app, Keycloak and the proxy from the bundle's files, published on this host's own address."""
    install.preflight(quadlet_dir)
    runtime = quadlet_dir / settings.KUBE_RUNTIME
    arguments = (project_root, quadlet_dir, runtime, None)
    changed = False
    for app in apps.APPS:
        changed = workloads.install_application(*arguments, node_address, service_port, app=app,
                                                target=target) or changed
    changed = workloads.install_keycloak(*arguments, target=target) or changed
    return workloads.install_shared_proxy(*arguments, node_address, service_port, target=target) or changed


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
    """Returns whether anything changed; each step refuses before the next can run.

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
    hostnames = target.hostnames
    replication.require_promoted_group(journal)
    missing = [name for name in transfer.replicated_names() if not exists('secret', name)]
    if missing:
        raise RuntimeError('Credentials required by the promoted group are missing: ' + ', '.join(missing))
    images_changed = images.prepare_offline_group(bundle_dir)
    workloads_changed = install_workloads(project_root, quadlet_dir, target, node_address, service_port)
    if images_changed or workloads_changed:
        run('systemctl', '--user', 'stop', *apps.services(databases=False), allowed=(0, 5))
    for service in [app.service for app in apps.APPS] + ['keycloak.service', 'shared-proxy.service']:
        quadlet.systemctl('start', service)
    for app in apps.APPS:
        require_application(app, hostnames[app.name])
    require_issuer(f'https://{hostnames[apps.SHARED_RESOURCE_OWNER.name]}:{service_port}/auth/realms/todo')
    clients_changed = keycloak.configure(secrets.read(apps.KEYCLOAK_ADMIN_SECRET),
                                         install.clients(apps.APPS, hostnames))
    target_render.write_record(target.values)
    certificate = run('podman', 'exec', 'nginx', 'cat', CA_CERTIFICATE).stdout.strip() + '\n'
    certificate_changed = quadlet.write(config_dir / 'todo-nginx-root.crt', certificate.encode(), 0o644)
    return images_changed or workloads_changed or clients_changed or certificate_changed
