"""Single-host uninstall, retaining DR refusal and opt-in database removal."""
import shutil
import socket
import sys
import time
from pathlib import Path

from . import apps, backup, install, secrets, settings, target_render, tls_secrets
from .commands import exists, run
from .quadlet import systemctl

# caddy-data is a retired Caddy-based proxy's volume name; kept here so a host
# still carrying it from before the nginx migration gets it cleaned up too.
TLS_VOLUMES = (apps.NGINX_TLS_VOLUME, 'todo-caddy-data')
# The old per-container install (the tag quadlet-reference-v1; its uninstaller
# was the retired Ansible playbook ansible/uninstall.yml). install.preflight
# refuses a host that has it; uninstall removes it, and says so.
OLD_NETWORK = 'todo-network'
OLD_IMAGE = 'localhost/todo-keycloak:m12'
# podman kube play makes a named volume of each ConfigMap it mounts as files, as
# of each secret (secrets.remove_kube_volumes); it is configuration, made again at every play.
CONFIG_VOLUMES = ('shared-nginx-config',)
# nginx's ports on 127.0.0.1, which its unit always publishes. Podman's port
# forwarder can hold one for a moment after the pod is gone; an install right
# after an uninstall (preflight.sh) must find them free, so uninstall waits.
PROXY_PORTS = (settings.LOCAL_HTTP_PORT, settings.HTTPS_PORT)
PORT_WAIT_SECONDS = 30


def backup_volumes(platform):
    """Every database's backup volume: what remove_backups deletes."""
    return tuple(database.volume('backup') for database in platform.replicated_databases)


def old_quadlet_files(platform):
    """The old per-container install's unit files."""
    return (*(name + '.container' for name in install.legacy_units(platform)), 'todo.network')


def quadlet_files(platform):
    """Every unit file uninstall removes from the Quadlet directory."""
    return (apps.NETWORK + '.network',
            *(database.volume(purpose) + '.volume' for database in platform.replicated_databases
              for purpose in ('data', 'backup')),
            *(volume + '.volume' for volume in TLS_VOLUMES),
            *old_quadlet_files(platform))


def services(platform):
    """Every workload's service, the network's, and the old install's: its containers', its
    network's (todo.network gives todo-network.service) and its data volume's."""
    return tuple(dict.fromkeys((*(workload.pod for workload in platform.workloads()), apps.NETWORK + '-network',
                                *install.legacy_units(platform), 'todo-network', 'todo-postgres-data-volume')))


def secret_names(platform):
    """The Kube secrets, the raw secrets they are made from, and nginx's TLS secrets (tls_secrets.py)."""
    mappings = secrets.kube_mappings(platform)
    return tuple(dict.fromkeys([*mappings, *(source for fields in mappings.values() for source in fields.values()),
                                *tls_secrets.secret_names()]))


def held_ports(ports):
    """The ports something holds on 127.0.0.1, found the way preflight.sh does: by binding them."""
    held = []
    for port in ports:
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                held.append(port)
    return held


def wait_for_ports(ports, seconds=PORT_WAIT_SECONDS):
    """Wait until the ports are free, at most seconds; return the ones still held."""
    deadline = time.monotonic() + seconds
    held = held_ports(ports)
    while held and time.monotonic() < deadline:
        time.sleep(1)
        held = held_ports(held)
    return held


def remove(kind, name):
    """Remove the Podman object if it exists; True if it did."""
    if exists(kind, name):
        run('podman', kind, 'rm', name)
        return True
    return False


def unlink(path):
    """Remove the file or symlink if it exists; True if it did."""
    existed = path.exists() or path.is_symlink()
    path.unlink(missing_ok=True)
    return existed


def uninstall(platform, remove_data=False, quadlet_dir=None, remove_backups=False):
    """Remove a single-host install of the platform: units, pods, containers, network and app images.

    Refuses on a host with replication, promotion or backup state: that is
    a DR node and needs a person to decide. The nightly backup timer goes;
    database volumes, TLS volumes and secrets are kept unless remove_data is
    True, and backup volumes unless remove_backups is True too, so
    reinstalling keeps the data and passwords. The Kube secrets' volumes
    always go (secrets.remove_kube_volumes), and so does an old
    per-container install (old_quadlet_files). The host's record of the
    platform goes with the data. When nginx was there, it returns once its
    ports are free again (wait_for_ports). Returns True if anything was removed.
    """
    if remove_backups and not remove_data:
        raise ValueError('Removing the backups needs remove_data too: a backup without its data is no use.')
    directory = Path(quadlet_dir or settings.QUADLET_DIR)
    install.require_single_host('uninstall', platform)
    old = [name for name in old_quadlet_files(platform) if (directory / name).exists()]
    stopped = run('systemctl', '--user', 'stop', *(name + '.service' for name in services(platform)),
                  allowed=(0, 5)).returncode == 0
    # The nightly backup timer goes; the backups themselves stay in their volumes.
    changed = backup.remove_timer() or stopped
    changed = any([unlink(directory / name) for name in quadlet_files(platform)]) or changed
    runtime = directory / settings.KUBE_RUNTIME
    if runtime.is_symlink():
        runtime.unlink()
        changed = True
    elif runtime.exists():
        shutil.rmtree(runtime)
        changed = True
    systemctl('daemon-reload')
    # Direct kube play has no systemd owner to remove its pods/infra containers.
    # Pod removal does not request volume deletion; PVCs follow remove_data below.
    proxy = exists('pod', 'shared-proxy') or exists('container', 'nginx')
    for name in (workload.pod for workload in reversed(platform.workloads())):
        changed = exists('pod', name) or changed
        run('podman', 'pod', 'rm', '--force', '--ignore', name)
    # nginx and the old install's containers, which kube play's pods did not start.
    for name in (*install.legacy_units(platform), 'nginx'):
        changed = exists('container', name) or changed
        run('podman', 'rm', '--force', '--ignore', name)
    held = wait_for_ports(PROXY_PORTS) if proxy else []
    if held:
        print(f'Port {", ".join(map(str, held))} on 127.0.0.1 is still held {PORT_WAIT_SECONDS} seconds '
              'after nginx was removed; an install checks it first (preflight.sh).', file=sys.stderr)
    changed = remove('network', apps.NETWORK) or changed
    changed = remove('network', OLD_NETWORK) or changed
    for name in CONFIG_VOLUMES:
        changed = remove('volume', name) or changed
    # The Kube secrets' volumes are copies of secrets, not data: they go now,
    # and the next install's kube play makes them again from the secrets.
    changed = secrets.remove_kube_volumes(platform) or changed
    changed = unlink(settings.DEV_STATE_FILE) or changed
    if remove_data:
        for database in platform.replicated_databases:
            changed = remove('volume', database.volume('data')) or changed
        for name in secret_names(platform):
            changed = remove('secret', name) or changed
        # nginx's CA and leaf are Podman secrets (secret_names() above) or, with the TLS
        # volume, in platform-nginx-data; docs/ARCHITECTURE.md lists both with the
        # database volumes as surviving local app recreation, so they are only
        # removed alongside them, not on a plain uninstall.
        for name in TLS_VOLUMES:
            changed = remove('volume', name) or changed
        # The public hostnames go with the data they were installed for; a plain
        # uninstall keeps them, so a reinstall serves the same names.
        changed = unlink(target_render.record_path()) or changed
        changed = unlink(target_render.platform_record_path()) or changed
    if remove_backups:
        for name in backup_volumes(platform):
            changed = remove('volume', name) or changed
    for app in platform.apps:
        for component in ('backend', 'frontend'):
            changed = remove('image', app.image(component)) or changed
    for reference in (apps.PROXY_IMAGE, apps.KEYCLOAK_IMAGE, OLD_IMAGE):
        changed = remove('image', reference) or changed
    if old:
        print('Removed the old per-container install (quadlet-reference-v1): ' + ', '.join(old)
              + f', its network {OLD_NETWORK} and its containers.', file=sys.stderr)
    return changed
