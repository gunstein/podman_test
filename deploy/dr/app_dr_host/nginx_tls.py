"""nginx's certificate from the organisation's CA (provided mode) on both hosts of a DR pair.

A single host gets provided mode from the installer (app_installer.tls_store,
docs/TLS.md). A DR pair needs more, because the standby runs only its
databases: it has no nginx until a failover starts the application tier
there. Started then without a certificate, nginx would make
a new demo CA, and every client would have to trust it during the incident.
So each host gets its own certificate, for the same public hostnames, before
it is needed:

  request   prepare the proxy image if the host has none (a standby), and
            with the TLS volume that volume too; make a key and return the CSR
  install   check and install the signed certificate
  provision before nginx starts on a promoted host: its files as Podman
            secrets (tls_secrets.provision; nothing with the TLS volume)
  set_mode  record the pair's mode on this host (PAIR_MODE): once both hosts
            hold their certificate, app-ops sets provided on both

With the pair in provided mode, app_dr.py check (both hosts, every 15
minutes) requires a certificate that fits this host's recorded hostnames,
deploy-promoted refuses to start nginx without one, and failover needs no
new client trust. The hostnames come from the host's record
(target-values.json), which a standby writes when it is bootstrapped.

Where the key and certificate live follows settings.NGINX_TLS_STORAGE
(app_installer.tls_store): Podman secrets on each host (tls_secrets), or the
TLS volume (tls), which stays for going back. They never cross to the other
host: each host makes its own key.
"""
from pathlib import Path

from app_installer import apps, images, settings, target_render, tls, tls_secrets, tls_store
from app_installer.commands import exists, run

# The pair's nginx TLS mode on this host: 'provided' once app-ops installed both hosts' certificates.
PAIR_MODE = Path.home() / settings.DR_CONFIG / 'nginx-tls-mode'


def pair_mode():
    """'provided' if app-ops recorded that this pair serves the organisation's certificates, else 'local'."""
    try:
        return PAIR_MODE.read_text().strip() or 'local'
    except FileNotFoundError:
        return 'local'


def set_mode(mode):
    """Record the pair's mode on this host; True if it changed."""
    if mode not in ('local', tls.PROVIDED):
        raise ValueError(f'Unknown nginx TLS mode: {mode}')
    if pair_mode() == mode:
        return False
    PAIR_MODE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    PAIR_MODE.write_text(mode + '\n')
    return True


def volume_claim(target):
    """The bundle's PersistentVolumeClaim for nginx's TLS volume, as YAML (the same as replication.data_claim)."""
    import yaml  # a DR-only dependency, as in replication.data_claim
    claims = [document for document in yaml.safe_load_all(target.manifests['shared-proxy.yaml'].decode())
              if isinstance(document, dict) and document.get('kind') == 'PersistentVolumeClaim'
              and document.get('metadata', {}).get('name') == apps.NGINX_TLS_VOLUME]
    if len(claims) != 1:
        raise ValueError(f'shared-proxy.yaml must define exactly one claim {apps.NGINX_TLS_VOLUME}')
    return yaml.safe_dump(claims[0])


def prepare(project_root, bundle_dir, node_address):
    """Give a host without nginx (a standby) the proxy image, and the TLS volume if used; True if anything changed.

    The image comes from the offline bundle, like every image a failover
    starts; every openssl step runs in it. With Podman secrets that is all.
    The TLS volume is created from the bundle's own claim, so it is exactly
    the one nginx will use after a failover, owned by the nginx user
    (volume.podman.io/uid).
    """
    changed = any(images.prepare_shared(bundle_dir, 'offline', bundle_dir).values())
    if tls_store.secret_storage():
        return changed
    if not exists('volume', apps.NGINX_TLS_VOLUME):
        target = target_render.load_on_host(project_root, node_address)
        run('podman', 'kube', 'play', '-', input=volume_claim(target))
        changed = True
    return changed


def request(project_root, bundle_dir, node_address):
    """A CSR for this host's recorded hostnames; the key waits in the TLS volume. Returns (csr, hostnames)."""
    prepare(project_root, bundle_dir, node_address)
    store = tls_store.module()
    store.require_ready()
    names = store.recorded_hostnames()
    return store.make_request(names), names


def install(project_root, bundle_dir, node_address, certificate, ca):
    """Check and install this host's signed certificate (install of tls_store.module()); True if anything changed.

    A running nginx (the primary) restarts with Podman secrets, or reloads
    with the TLS volume; a standby's nginx reads it at its first start,
    after a failover.
    """
    prepare(project_root, bundle_dir, node_address)
    store = tls_store.module()
    return store.install(certificate, ca, hostnames=store.recorded_hostnames())


def provision(hostnames, identity):
    """Before nginx starts on a promoted host: its TLS files as Podman secrets; True if they changed.

    hostnames is {app name: hostname}, identity Keycloak's hostname. In local mode this makes the
    promoted host its own demo CA (backlog T4), in provided mode it only
    publishes what nginx-tls install put there. With the TLS volume the
    pod's init container does this, so nothing happens here.
    """
    if not tls_store.secret_storage():
        return False
    return tls_secrets.provision(tls_secrets.ordered(hostnames, identity))


def fitting():
    """(days left, '') if this host holds a certificate from your CA that fits its recorded hostnames, else (None, why)."""
    store = tls_store.module()
    current, days, problem = store.status(store.recorded_hostnames())
    if problem or current != tls.PROVIDED or days is None:
        return None, problem or 'this host has no certificate from your CA'
    return days, ''


def readiness():
    """Could nginx start here with the organisation's certificate? Returns (lines, problems), like app_dr.check.

    Only for a pair in provided mode: then this host must hold a certificate
    that fits its recorded hostnames, with at least tls.ALERT_DAYS left.
    """
    if pair_mode() != tls.PROVIDED:
        return [], []
    days, problem = fitting()
    if days is None:
        return [], [f'nginx could not start here with your CA\'s certificate: {problem}; '
                    'install it with app-ops nginx-tls-request and nginx-tls-install']
    if days < tls.ALERT_DAYS:
        return [], [f'the nginx certificate on this host expires in {days} days; '
                    'renew it with app-ops nginx-tls-request and nginx-tls-install']
    return [f'nginx certificate from your CA: valid {days} more days'], []


def require_for_failover():
    """Raise unless a pair in provided mode has a fitting certificate here; deploy-promoted calls it first."""
    if pair_mode() != tls.PROVIDED:
        return
    days, problem = fitting()
    if days is None:
        raise RuntimeError('This pair serves certificates from your CA, but this host has none that fits '
                           f'({problem}). Starting nginx would make a new demo CA that '
                           'no client trusts; install this host\'s certificate first (app-ops nginx-tls-install).')
