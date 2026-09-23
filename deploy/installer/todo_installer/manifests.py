"""Direct Jinja2 rendering of Kube manifests, replacing Helm as a template engine.

Kubernetes YAML here is only a manifest format for podman kube play/Quadlet;
there is never a real cluster, so a template engine is all "helm template"
ever provided. Ownership stays split the same way as the Quadlet templates:
stack.py/apps.py own topology and naming, values.yaml owns per-environment
settings, and only truly static structure lives in the .j2 files themselves.
"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def _environment(project_root):
    return Environment(
        loader=FileSystemLoader(Path(project_root) / "deploy/manifests"),
        undefined=StrictUndefined, trim_blocks=True, keep_trailing_newline=True,
        autoescape=False,
    )


def _render(project_root, name, **variables):
    return _environment(project_root).get_template(name).render(**variables).encode()


def render_postgres(project_root, database, image):
    return _render(project_root, "postgres.yaml.j2", database=database, image=image)


def render_postgres_config(project_root, database):
    return _render(project_root, "postgres-config.yaml.j2", database=database)


def render_app(project_root, app, backend_image, frontend_image):
    return _render(project_root, "app.yaml.j2", app=app,
                   backend_image=backend_image, frontend_image=frontend_image)


def render_app_config(project_root, app, hostname, port, log_level):
    return _render(project_root, "app-config.yaml.j2", app=app,
                   hostname=hostname, port=port, log_level=log_level)


def render_keycloak(project_root, database, admin_secret, hostname, port, image):
    return _render(project_root, "keycloak.yaml.j2", database=database, admin_secret=admin_secret,
                   hostname=hostname, port=port, image=image)


def render_shared_proxy(project_root, applications, identity_app, hostname, image):
    context = [{
        "name": app.name,
        "hostname": hostname if app is identity_app else app.hostname,
        "frontend": app.resource("app") + ":8080",
        "backend": app.resource("app") + ":8000",
    } for app in applications]
    return _render(project_root, "shared-proxy.yaml.j2", applications=context, hostname=hostname, image=image)
