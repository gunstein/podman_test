"""Install shared workload definitions; callers own safe stop/start ordering.

Each install function returns whether a definition changed (not secret
creation or removal of obsolete files); callers use it to decide what to
restart.

A workload's files come from one of two places. In build mode, the Kube YAML
from rendered_manifest_dir and units rendered here from deploy/quadlet with
Jinja2. With target=, an offline bundle's files, rendered at build time and
filled in with the target values (target_render.TargetFiles); then this host
needs no Jinja2. The offline install and the DR tools always pass target.
Either way the same staging, comparison and permissions apply.
"""
import ipaddress
from pathlib import Path

from . import apps, quadlet, secrets, settings, stack
from .commands import run


def postgres_variables(database, publish_address=""):
    """The unit template's values for one database: its replication port, and the LAN address if any."""
    return {"postgres_publish_address": publish_address, "postgres_publish_port": database.replication_port}


def application_variables(publish_address, service_port):
    """The unit template's values for an app pod."""
    return {"todo_publish_address": publish_address, "todo_service_port": service_port}


def proxy_variables(publish_address, service_port, applications):
    """The unit template's values for nginx: where it publishes HTTPS and the app services it needs."""
    return {"todo_publish_address": publish_address, "todo_service_port": service_port,
            "app_services": [app.service for app in applications]}


def _install(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir, *,
             manifests, units, obsolete, capability, mapping, variables, values=None, target=None,
             replicated=False):
    """Write one workload's Kube YAML and Quadlet unit, and reload user systemd.

    Every file is read and rendered before the first write, so a missing
    file stops the install with nothing changed. With target, the files are
    the bundle's, already filled in (see the module docstring); replicated
    picks the database unit that also publishes replication. Kube
    secrets are created from the raw Podman secrets; YAML is written 0600,
    units 0644. Returns True if a definition changed. It never starts or
    stops a service: the caller decides that.
    """
    root, directory, runtime = map(Path, (project_root, quadlet_dir, kube_runtime_dir))
    if runtime != directory / settings.KUBE_RUNTIME or runtime.is_symlink():
        raise ValueError(f"kube_runtime_dir must be quadlet_dir/{settings.KUBE_RUNTIME}")
    if "--no-pod-prefix" not in run("podman", "kube", "play", "--help").stdout:
        raise RuntimeError(f"The {capability} Kube runtime requires Podman --no-pod-prefix.")
    # install.preflight() has already refused a host with legacy per-container
    # Quadlets (install.install and the install-workload command call it first).
    # Read and render everything before mutating the installation.
    if target is None:
        files = [(runtime / name, (Path(rendered_manifest_dir) / name).read_bytes(), 0o600)
                 for name in manifests]
        files += [(directory / "app-network.network",
                   (root / "deploy/quadlet/app-network.network").read_bytes(), 0o644)]
        files += [(runtime / name, quadlet.render(root, name, variables), 0o644) for name in units]
    else:
        files = [(runtime / name, target.manifests[name], 0o600) for name in manifests]
        files += [(directory / target.network_name, target.network, 0o644)]
        files += [(runtime / name, (target.replicated if replicated else target.quadlets)[name], 0o644)
                  for name in units]
    secrets.create_kube(mapping, values)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime.mkdir(exist_ok=True, mode=0o700)
    runtime.chmod(0o700)
    changed = False
    for path, content, mode in files:
        changed = quadlet.write(path, content, mode) or changed
    for name in obsolete:
        (directory / (name + ".volume")).unlink(missing_ok=True)
    quadlet.systemctl("daemon-reload")
    return changed


def install_postgres(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
                     publish_address="", db_password=None, *,
                     database: stack.Database = apps.SHARED_RESOURCE_OWNER.database, target=None):
    """Install one database's PostgreSQL workload.

    publish_address publishes the replication port on the LAN; only
    databases in the DR group may do that. With target, the bundle's
    replicated unit publishes on the target's own publish address, so
    publish_address must be that address. db_password supplies the owner
    password directly instead of reading it from Podman.
    """
    if publish_address and database not in apps.REPLICATED_DATABASES:
        raise ValueError("Replication publication requires membership in the verified DR group.")
    if publish_address and target is not None and publish_address != target.values["TARGET_PUBLISH_ADDRESS"]:
        raise ValueError(f"The bundle's units publish on {target.values['TARGET_PUBLISH_ADDRESS']}, "
                         f"not on {publish_address}.")
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        manifests=(database.manifest, database.config_manifest), units=(database.unit,),
        obsolete=(database.volume("data"), database.volume("backup")),
        capability="PostgreSQL", mapping=secrets.postgres_secret_mapping(database),
        variables=postgres_variables(database, publish_address),
        values={database.secret("db"): db_password} if db_password is not None else None, target=target,
        replicated=bool(publish_address),
    )


def install_application(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
                        publish_address="127.0.0.1", service_port=settings.HTTPS_PORT, *,
                        app: apps.App = apps.APPS[0], target=None):
    """Install one app's pod (migration, backend and frontend)."""
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        manifests=(app.manifest, app.config_manifest), units=(app.unit,), obsolete=(),
        capability="application", mapping=secrets.application_secret_mapping(app),
        variables=application_variables(publish_address, service_port), target=target,
    )


def install_keycloak(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir, *, target=None):
    """Install the shared Keycloak workload."""
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        manifests=("keycloak.yaml",), units=("keycloak.kube",), obsolete=(),
        capability="identity", mapping=secrets.keycloak_secret_mapping(), variables={}, target=target,
    )


def install_shared_proxy(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
                         publish_address="127.0.0.1", service_port=settings.HTTPS_PORT,
                         applications=None, *, target=None):
    """Install the shared nginx proxy, published on publish_address:service_port.

    It always also listens on 127.0.0.1, so a wildcard address such as
    0.0.0.0 is refused: it would bind the same port twice.
    """
    # The rendered unit always keeps a fixed 127.0.0.1 binding on
    # settings.LOCAL_HTTP_PORT/HTTPS_PORT alongside the requested one
    # (deploy/quadlet/shared-proxy.kube.j2); a wildcard address here would
    # bind the HTTPS port twice and podman would refuse to start the service.
    if publish_address != "127.0.0.1" and ipaddress.ip_address(publish_address).is_unspecified:
        raise ValueError(
            f"publish_address must not be a wildcard address ({publish_address!r}); "
            "it would collide with the fixed 127.0.0.1 binding. Use the host's own address."
        )
    applications = apps.APPS if applications is None else applications
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        manifests=("shared-proxy.yaml", "config.yaml"), units=("shared-proxy.kube",),
        obsolete=(apps.SHARED_RESOURCE_OWNER.names.resource("nginx-data"),),
        capability="shared proxy", mapping={},
        variables=proxy_variables(publish_address, service_port, applications), target=target,
    )
