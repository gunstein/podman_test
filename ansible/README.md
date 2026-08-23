# Ansible deployment

This playbook deploys the complete application to the current user on localhost.
It uses only modules included with `ansible-core`.

## Install Ansible

From the project root:

```bash
python3 -m venv ansible/.venv
ansible/.venv/bin/python -m pip install -r ansible/requirements.txt
```

## Deploy

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/deploy.yml
```

On the first run, the playbook asks for the PostgreSQL password and an initial
Keycloak administrator password without echoing them. It creates rootless Podman
secrets and generates independent passwords for the migration, backend and
Keycloak database roles. It builds the backend, frontend and Keycloak images,
installs the Quadlet files, starts the service chain and verifies health,
database readiness and Keycloak discovery.

A normal repeat deploy is idempotent relative to the images already stored
locally. It does not check registries for security updates. Explicitly rebuild
the application images, refresh their base images and pull PostgreSQL with:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/deploy.yml \
  --extra-vars refresh_images=true
```

Image refresh is intentionally unavailable in offline mode because the bundle is
the complete, fixed source of images there.

The playbook does not install Podman, enable lingering, rotate an existing secret or modify system-wide configuration. Those operations require separate administrative decisions.

The Keycloak account created from the prompted password is its temporary
bootstrap administrator. This localhost demo retains it for repeatable
administration and E2E setup; a real deployment should replace and remove it.

## Uninstall

Remove the deployed services, Quadlet files, containers, network and application
images:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/uninstall.yml
```

The persistent `todo-postgres-data` volume and its database-related Podman
secrets are preserved by default. Existing database data requires its existing
credentials when the application is installed again. Permanently delete the
database and its related secrets only when that is intentional:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/uninstall.yml \
  --extra-vars remove_data=true
```

The second command permanently deletes all Todo and Keycloak data.

Both uninstall modes remove `todo-caddy-data`. A reinstall creates a new local
Caddy CA, so any previously trusted demo root certificate must be replaced.
