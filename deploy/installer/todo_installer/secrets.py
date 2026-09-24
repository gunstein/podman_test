"""Construct Podman Kube-compatible secrets entirely in memory."""
import base64
import json

from . import apps
from .commands import exists, run


def postgres_secret_mapping(app: apps.App):
    return {app.kube_secret("postgres"): {"database-password": app.secret("db")}}


def application_secret_mapping(app: apps.App):
    return {
        app.kube_secret("migrator"): {"database-password": app.secret("migrator")},
        app.kube_secret("backend"): {"database-password": app.secret("app")},
    }


def keycloak_secret_mapping():
    return {apps.KEYCLOAK_KUBE_ADMIN_SECRET: {"bootstrap-admin-password": apps.KEYCLOAK_ADMIN_SECRET}}


def read(name):
    # Ansible command.stdout strips trailing newlines, but preserves other whitespace.
    return run("podman", "secret", "inspect", "--showsecret", "--format",
               "{{.SecretData}}", name).stdout.rstrip("\r\n")


def create_kube(mapping, values=None):
    changed = False
    values = values or {}
    for name, fields in mapping.items():
        data = {}
        for key, source in fields.items():
            raw = values[source] if source in values else read(source)
            data[key] = base64.b64encode(raw.encode()).decode()
        if not exists("secret", name):
            payload = {"apiVersion": "v1", "kind": "Secret",
                       "metadata": {"name": name}, "data": data}
            run("podman", "secret", "create", name, "-", input=json.dumps(payload))
            changed = True
    return changed


def provision(applications=None):
    """Keep existing credentials; generate every missing password."""
    import secrets as random
    import string

    applications = apps.APPS if applications is None else applications
    names = [app.secret(role) for app in applications for role in ("db", "migrator", "app")]
    names.extend((apps.KEYCLOAK_DATABASE.secret("db"), apps.KEYCLOAK_ADMIN_SECRET))
    for name in names:
        if exists('secret', name):
            continue
        value = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(32))
        run('podman', 'secret', 'create', name, '-', input=value)
