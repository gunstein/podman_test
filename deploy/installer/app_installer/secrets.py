"""Construct Podman Kube-compatible secrets entirely in memory."""
import base64
import json

from . import apps, stack
from .commands import exists, run


def postgres_secret_mapping(database: stack.Database):
    """Kube secret name -> {key: raw Podman secret} for the PostgreSQL pod."""
    return {database.kube_secret: {"database-password": database.secret("db")}}


def application_secret_mapping(app: apps.App):
    """The same for the app pod: the migrator's and the backend's passwords."""
    return {
        app.names.kube_secret("migrator"): {"database-password": app.database.secret("migrator")},
        app.names.kube_secret("backend"): {"database-password": app.database.secret("app")},
    }


def keycloak_secret_mapping():
    """The same for Keycloak's bootstrap admin password."""
    return {apps.KEYCLOAK_KUBE_ADMIN_SECRET: {"bootstrap-admin-password": apps.KEYCLOAK_ADMIN_SECRET}}


def read(name):
    """The value of a raw Podman secret. Only for passing on to another secret, never for printing."""
    # Strip only trailing newlines, as the value was stored; keep other whitespace.
    return run("podman", "secret", "inspect", "--showsecret", "--format",
               "{{.SecretData}}", name).stdout.rstrip("\r\n")


def create_kube(mapping, values=None):
    """Create each missing Kube secret from the raw Podman secrets it maps to.

    podman kube play reads these secrets; the values are base64-encoded as
    Kubernetes expects, and exist only in memory and in Podman's secret
    store, never in a file. values can supply raw values directly.
    An existing Kube secret must already hold the same values. If one
    differs, nothing is created and the error names it, never its values.
    Returns True if a secret was created.
    """
    values = values or {}
    wanted = {}
    for name, fields in mapping.items():
        wanted[name] = {}
        for key, source in fields.items():
            raw = values[source] if source in values else read(source)
            wanted[name][key] = base64.b64encode(raw.encode()).decode()
    missing = [name for name in wanted if not exists("secret", name)]
    different = [name for name in wanted if name not in missing and _kube_data(name) != wanted[name]]
    if different:
        raise RuntimeError(
            "Kube secrets differ from the Podman secrets they are made from: " + ", ".join(different)
            + ". No secret was created; find out which value the databases use before removing either.")
    for name in missing:
        payload = {"apiVersion": "v1", "kind": "Secret",
                   "metadata": {"name": name}, "data": wanted[name]}
        run("podman", "secret", "create", name, "-", input=json.dumps(payload))
    return bool(missing)


def _kube_data(name):
    """The data of an existing Kube secret, or None if it does not hold one."""
    try:
        return json.loads(read(name)).get("data")
    except (ValueError, AttributeError):
        return None


def installed_names(applications=None):
    """The raw Podman secrets an install creates: each app's three database
    passwords, Keycloak's database password and its admin password."""
    applications = apps.APPS if applications is None else applications
    names = [app.database.secret(role) for app in applications for role in ("db", "migrator", "app")]
    return names + [apps.KEYCLOAK_DATABASE.secret("db"), apps.KEYCLOAK_ADMIN_SECRET]


def provision(applications=None):
    """Keep existing credentials; generate every missing password."""
    import secrets as random
    import string

    for name in installed_names(applications):
        if exists('secret', name):
            continue
        value = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(32))
        run('podman', 'secret', 'create', name, '-', input=value)
