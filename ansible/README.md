# Ansible deployment

This first Ansible milestone deploys to the current user on localhost. It uses only modules included with `ansible-core` and keeps the same rootless Podman setup used manually in M6 and M7.

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

On the first run, the playbook asks for the existing PostgreSQL password without echoing it. It creates the rootless `todo-db-password` secret, builds both images, installs the Quadlet files, starts the service chain and verifies health and readiness.

Later runs reuse the existing secret and images. To deliberately rebuild a milestone image, remove that local image before running the playbook again.

The playbook does not install Podman, enable lingering, rotate an existing secret or modify system-wide configuration. Those operations require separate administrative decisions.

## Uninstall

Remove the deployed services, Quadlet files, containers, network, M8 images and Podman secret:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/uninstall.yml
```

The persistent `todo-postgres-data` volume is preserved by default. Permanently delete the database only when that is intentional:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/uninstall.yml \
  --extra-vars remove_data=true
```

The second command permanently deletes all Todo data.
