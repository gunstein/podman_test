"""Install shared workload definitions; callers own safe stop/start ordering.

As in the original shared roles, changed reports definition changes (not secret
creation or removal of obsolete files). DR uses it to decide when to restart.
"""
from pathlib import Path

from . import apps, quadlet, secrets
from .commands import run


def _install(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
             manifests, units, obsolete, legacy, message, capability, mapping,
             variables, values=None):
    root, directory, runtime, rendered = map(Path, (
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir))
    if runtime != directory / "todo-kube-runtime" or runtime.is_symlink():
        raise ValueError("kube_runtime_dir must be quadlet_dir/todo-kube-runtime")
    if "--no-pod-prefix" not in run("podman", "kube", "play", "--help").stdout:
        raise RuntimeError(f"The {capability} Kube runtime requires Podman --no-pod-prefix.")
    if any((directory / name).exists() for name in legacy):
        raise RuntimeError(message)
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
    legacy = app.resource("postgres") + ".container"
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        (app.manifest("postgres"), app.manifest("config")), (app.unit("postgres"),),
        (app.volume("data"), app.volume("backup")), (legacy,),
        f"Unsupported {legacy} is installed. Stop and review the host "
        "separately; this operation does not migrate an existing PostgreSQL runtime.",
        "PostgreSQL", secrets.postgres_secret_mapping(app),
        {"todo_postgres_publish_address": publish_address,
         "postgres_publish_port": app.replication_port},
        {app.secret("db"): db_password} if db_password is not None else None,
    )


def install_application(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
                        publish_address="127.0.0.1", service_port=8443, *,
                        app: apps.App = apps.APPS[0]):
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        (app.manifest("app"), app.manifest("config")), (app.unit("app"),), (),
        tuple(app.resource(component) + ".container" for component in ("backend", "frontend")),
        "Unsupported application container Quadlets are installed. Stop and review "
        "the host separately before installing the grouped Kube application.",
        "application", secrets.application_secret_mapping(app),
        {"todo_publish_address": publish_address, "todo_service_port": service_port},
    )


def install_keycloak(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir):
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        ("keycloak.yaml",), ("keycloak.kube",), (),
        ("todo-keycloak.container", "keycloak.container"),
        "Unsupported identity container Quadlet is installed. Stop and review the host separately.",
        "identity", secrets.keycloak_secret_mapping(), {},
    )


def install_shared_proxy(project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
                         publish_address="127.0.0.1", service_port=8443, applications=None):
    applications = apps.REPLICATED_APPS if applications is None else applications
    return _install(
        project_root, quadlet_dir, kube_runtime_dir, rendered_manifest_dir,
        ("shared-proxy.yaml", "config.yaml"), ("shared-proxy.kube",),
        (apps.IDENTITY_DATABASE_APP.resource("nginx-data"),),
        (apps.IDENTITY_DATABASE_APP.resource("frontend") + ".container",),
        "Unsupported legacy frontend container Quadlet is installed. Stop and "
        "review the host separately before installing the shared Kube proxy.",
        "shared proxy", {},
        {"todo_publish_address": publish_address, "todo_service_port": service_port,
         "app_services": [app.service("app") for app in applications]},
    )
