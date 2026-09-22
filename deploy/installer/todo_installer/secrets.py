"""Construct Podman Kube-compatible secrets entirely in memory."""
import base64
import json

from .commands import exists, run

POSTGRES = {"todo-kube-postgres-secret": {"database-password": "todo-db-password"}}
APPLICATION = {
    "todo-kube-migrator-secret": {"database-password": "todo-migrator-password"},
    "todo-kube-backend-secret": {"database-password": "todo-app-password"},
    "todo-kube-keycloak-secret": {
        "database-password": "todo-keycloak-db-password",
        "bootstrap-admin-password": "todo-keycloak-admin-password",
    },
}


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


def provision():
    """Keep existing credentials; prompt for administrators, generate role passwords."""
    import getpass
    import secrets as random
    import string
    import sys

    generated = ('todo-migrator-password', 'todo-app-password', 'todo-keycloak-db-password')
    for name in ('todo-db-password', *generated, 'todo-keycloak-admin-password'):
        if exists('secret', name):
            continue
        if name in generated:
            value = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(32))
        else:
            if not sys.stdin.isatty():
                raise RuntimeError(f'Missing {name}: an interactive terminal is required to enter '
                                   'the password. Provision the Podman secret before retrying.')
            prompt = ('Database password: ' if name == 'todo-db-password'
                      else 'Initial Keycloak admin password: ')
            value = getpass.getpass(prompt)
        run('podman', 'secret', 'create', name, '-', input=value)
