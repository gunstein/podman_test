"""Install shared workload definitions; callers own safe stop/start ordering.

As in the original shared roles, changed reports definition changes (not secret
creation or removal of obsolete files). DR uses it to decide when to restart.
"""
import ipaddress
from pathlib import Path

from . import apps, quadlet, secrets, settings
from .commands import run


def _install(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir, *,
             manifests, units, obsolete, capability, mapping, variables, values=None):
    root, directory, runtime, rendered = map(Path, (
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir))
    if runtime != directory / "todo-kube-runtime" or runtime.is_symlink():
        raise ValueError("kube_runtime_dir must be quadlet_dir/todo-kube-runtime")
    if "--no-pod-prefix" not in run("podman", "kube", "play", "--help").stdout:
        raise RuntimeError(f"The {capability} Kube runtime requires Podman --no-pod-prefix.")
    # install.preflight() already refuses a legacy per-container Quadlet host-wide,
    # before any workload install runs; see install-workload's CLI dispatch for the
    # DR/Ansible path, which calls it for the same reason.
    # Read and render everything before mutating the installation.
    files = [(runtime / name, (rendered / name).read_bytes(), 0o600)
             for name in manifests]
    files += [(directory / "app-network.network", (root / "deploy/quadlet/app-network.network").read_bytes(),
               0o644)]
    files += [(runtime / name, quadlet.render(root, name, variables), 0o644) for name in units]
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
                     publish_address="", db_password=None, *, app: apps.App = apps.APPS[0]):
    if publish_address and app.name not in {d.name for d in apps.REPLICATED_DATABASES}:
        raise ValueError("Replication publication requires membership in the verified DR group.")
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        manifests=(app.manifest("postgres"), app.manifest("config")), units=(app.unit("postgres"),),
        obsolete=(app.volume("data"), app.volume("backup")),
        capability="PostgreSQL", mapping=secrets.postgres_secret_mapping(app),
        variables={"todo_postgres_publish_address": publish_address,
                  "postgres_publish_port": app.replication_port},
        values={app.secret("db"): db_password} if db_password is not None else None,
    )


def install_application(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
                        publish_address="127.0.0.1", service_port=settings.HTTPS_PORT, *,
                        app: apps.App = apps.APPS[0]):
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        manifests=(app.manifest("app"), app.manifest("config")), units=(app.unit("app"),), obsolete=(),
        capability="application", mapping=secrets.application_secret_mapping(app),
        variables={"todo_publish_address": publish_address, "todo_service_port": service_port},
    )


def install_keycloak(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir):
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        manifests=("keycloak.yaml",), units=("keycloak.kube",), obsolete=(),
        capability="identity", mapping=secrets.keycloak_secret_mapping(), variables={},
    )


def install_shared_proxy(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
                         publish_address="127.0.0.1", service_port=settings.HTTPS_PORT,
                         applications=None):
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
        obsolete=(apps.IDENTITY_DATABASE_APP.resource("nginx-data"),),
        capability="shared proxy", mapping={},
        variables={"todo_publish_address": publish_address, "todo_service_port": service_port,
                  "app_services": [app.service("app") for app in applications]},
    )
