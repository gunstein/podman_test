"""Direct Jinja2 rendering of Kube manifests, on the build host only.

Kubernetes YAML here is only a manifest format for podman kube play/Quadlet;
there is never a real cluster, so a template engine is all that is needed
and Helm is not used. Ownership is split the same way as for the Quadlet templates:
stack.py/apps.py own topology and naming, values.yaml owns per-environment
settings, and only truly static structure lives in the .j2 files themselves.
"""
import re
from pathlib import Path

from . import apps

# shared-proxy.yaml.j2 interpolates hostnames raw into the nginx.conf literal
# block scalar (plain text, not a YAML value, so | tojson does not apply
# there). Validate every hostname that reaches it - both the operator-supplied
# runtime.identityHostname and each App's own registry hostname - so neither can
# inject an nginx directive or break the surrounding YAML with a stray ';',
# '#' or newline. The check also requires a real DNS name, so a name such as
# ".." or "-todo.test" fails here and not later in nginx, TLS or Keycloak.
#
# One DNS label: lowercase letters and digits, with hyphens only inside, at
# most 63 characters. fullmatch, because '$' would also accept a final newline.
DNS_LABEL = re.compile(r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?')


def validate_hostname(hostname):
    """Raise unless hostname is a DNS name: labels as above, joined by single dots."""
    if not (isinstance(hostname, str) and len(hostname) <= 253
            and all(DNS_LABEL.fullmatch(label) for label in hostname.split('.'))):
        raise ValueError(f'Not a safe hostname: {hostname!r}')


def _environment(project_root):
    """A Jinja2 environment for deploy/manifests; undefined variables are an error.

    Jinja2 is imported here, not at the top, so validate_hostname and the
    offline install (target_render) work on a host without it.
    """
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    return Environment(
        loader=FileSystemLoader(Path(project_root) / "deploy/manifests"),
        undefined=StrictUndefined, trim_blocks=True, keep_trailing_newline=True,
        autoescape=False,
    )


def _render(project_root, name, **variables):
    """Render one template and return the bytes."""
    return _environment(project_root).get_template(name).render(**variables).encode()


def render_postgres(project_root, database, image):
    """The PostgreSQL pod, its data and backup volume claims, for one database."""
    return _render(project_root, "postgres.yaml.j2", database=database, image=image)


def render_postgres_config(project_root, database):
    """The ConfigMap that PostgreSQL's pod reads its settings from."""
    return _render(project_root, "postgres-config.yaml.j2", database=database)


def render_app(project_root, app, backend_image, frontend_image):
    """One app's pod: the migration init container, the backend and the frontend."""
    return _render(project_root, "app.yaml.j2", app=app,
                   backend_image=backend_image, frontend_image=frontend_image)


def render_app_config(project_root, app, hostname, port, log_level):
    """The ConfigMap the app's backend and frontend read (hostname, port, OIDC settings)."""
    return _render(project_root, "app-config.yaml.j2", app=app,
                   hostname=hostname, port=port, log_level=log_level)


def render_keycloak(project_root, database, admin_secret, hostname, port, image):
    """The Keycloak pod, using its own database and the admin secret."""
    return _render(project_root, "keycloak.yaml.j2", database=database, admin_secret=admin_secret,
                   hostname=hostname, port=port, image=image)


def render_shared_proxy(project_root, applications, hostnames, identity_hostname, port, image):
    """The nginx pod that terminates TLS and routes each hostname to its app, and Keycloak's to Keycloak.

    hostnames maps each app's name to its public hostname; identity_hostname
    is where Keycloak serves every app's login and tokens, which goes into
    the CSP. Every hostname is checked before it is written into
    nginx.conf, except a ${TARGET_...} placeholder of an offline bundle: the
    host checks the value that replaces it (target_render.check_hostname)
    before anything is installed.
    """
    context = [{
        "name": app.name,
        "hostname": hostnames[app.name],
        "frontend": app.pod + ":8080",
        "backend": app.pod + ":8000",
    } for app in applications]
    for hostname in [identity_hostname] + [entry["hostname"] for entry in context]:
        if not hostname.startswith("${TARGET_"):
            validate_hostname(hostname)
    hostname = identity_hostname
    return _render(project_root, "shared-proxy.yaml.j2", applications=context, hostname=hostname,
                   identity_origin=f"https://{hostname}:{int(port)}", image=image,
                   tls_secret=apps.PROXY_KUBE_TLS_SECRET)
