"""Deploy the application tier on the host whose databases were promoted as a group."""
import time
from pathlib import Path

from . import apps, images, install, keycloak, preflight, quadlet, replication, secrets, workloads
from .commands import exists, run

CA_CERTIFICATE = '/var/lib/todo-tls/ca.crt'
DISCOVERY = '/auth/realms/todo/.well-known/openid-configuration'


def require_identity(inventory_hostname, node_address, service_port):
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


def install_workloads(project_root, quadlet_dir, rendered, node_address, service_port):
    install.preflight(quadlet_dir)
    runtime = quadlet_dir / 'todo-kube-runtime'
    arguments = (project_root, quadlet_dir, runtime, rendered)
    changed = False
    for app in apps.APPS:
        changed = workloads.install_application(*arguments, node_address, service_port, app=app) or changed
    changed = workloads.install_keycloak(*arguments) or changed
    return workloads.install_shared_proxy(*arguments, node_address, service_port) or changed


def require_application(app):
    """Health, database readiness and a public read, all through the shared nginx virtual host."""
    keycloak.wait('/health', 30, 1, 'ok', hostname=app.hostname)
    keycloak.wait('/ready', 30, 1, 'ready', hostname=app.hostname)
    if not isinstance(keycloak.request(app.api_path(), hostname=app.hostname), list):
        raise RuntimeError(f'{app.name}: public read {app.api_path()} did not return a list')


def require_issuer(issuer, attempts=90, delay=2):
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
    """Returns whether anything changed; each step refuses before the next can run."""
    project_root, quadlet_dir, bundle_dir, config_dir = map(Path, (project_root, quadlet_dir, bundle_dir, config_dir))
    require_identity(inventory_hostname, node_address, service_port)
    replication.require_promoted_group(journal)
    missing = [name for name in secrets.replicated_names() if not exists('secret', name)]
    if missing:
        raise RuntimeError('Credentials required by the promoted group are missing: ' + ', '.join(missing))
    images_changed = images.prepare_offline_group(bundle_dir)
    workloads_changed = install_workloads(project_root, quadlet_dir, bundle_dir / 'generated/kube-runtime',
                                          node_address, service_port)
    if images_changed or workloads_changed:
        run('systemctl', '--user', 'stop', *apps.services(databases=False), allowed=(0, 5))
    for service in [app.service('app') for app in apps.APPS] + ['keycloak.service', 'shared-proxy.service']:
        quadlet.systemctl('start', service)
    for app in apps.APPS:
        require_application(app)
    require_issuer(f'https://{apps.SHARED_RESOURCE_OWNER.hostname}:{service_port}/auth/realms/todo')
    clients_changed = keycloak.configure(secrets.read(apps.KEYCLOAK_ADMIN_SECRET),
                                         [(app.keycloak_client, app.hostname) for app in apps.APPS])
    certificate = run('podman', 'exec', 'nginx', 'cat', CA_CERTIFICATE).stdout.strip() + '\n'
    certificate_changed = quadlet.write(config_dir / 'todo-nginx-root.crt', certificate.encode(), 0o644)
    return images_changed or workloads_changed or clients_changed or certificate_changed
