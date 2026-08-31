# Ansible deployment

This playbook deploys the complete application to the current user on localhost.
It uses only modules included with `ansible-core`. The project-level
`ansible.cfg` pins `/usr/bin/python3` and enables pipelining for local and SSH
connections. This keeps transport behavior consistent across milestones and
avoids transient Ansible Python files on hosts protected by `fapolicyd`.

## Install Ansible

From the project root:

```bash
python3 -m venv ansible/.venv
ansible/.venv/bin/python -m pip install -r ansible/requirements.txt
```

## Optional authoritative secret input

The default lab generates missing credentials on the first host. For a
moderate Ansible-managed installation, pre-provision the host-local Podman
secrets from an encrypted input before deployment:

```bash
cp ansible/secrets.example.yml ansible/secrets.yml
# Replace placeholders, then encrypt before use.
ansible-vault encrypt ansible/secrets.yml
ansible-playbook \
  --inventory ansible/inventory.ini \
  --ask-vault-pass \
  --extra-vars @ansible/secrets.yml \
  ansible/provision-secrets.yml
```

The playbook creates only missing secrets and rejects mismatches. Read
[../docs/SECRETS.md](../docs/SECRETS.md) for two-host provisioning, rotation,
runtime file-versus-environment delivery and recovery boundaries.

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

The frontend Quadlet is the single `default.target` entrypoint and pulls in the
remaining services. It starts with the user's systemd manager after login. For
boot-before-login operation on an always-on host, an administrator must run
`sudo loginctl enable-linger <service-user>`.

The playbook does not install Podman, enable lingering, rotate an existing
secret or modify system-wide configuration. Those operations require separate
administrative decisions.

The Keycloak account created from the prompted password is its temporary
bootstrap administrator. This localhost demo retains it for repeatable
administration and E2E setup; a real deployment should replace and remove it.

## M12 uninstall

`uninstall.yml` is intentionally limited to the single-host M12 deployment. It
refuses to run when it detects M13-M16 replication, promotion, backup or
rebuilt-standby state.
Do not treat a promoted database or its backup archive as ordinary M12 data.

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

Both uninstall modes remove `todo-nginx-data`. A reinstall creates a new
demo CA, so any previously trusted demo root certificate must be replaced.

## Operations package and inventories

Build one source-only package for standby bootstrap, promotion, application
failover, backup and standby rebuild:

```bash
scripts/build-operations-package.sh
```

The package provides two inventory templates:

- `inventory-initial.example.ini` describes the original primary and standby
  before the first replication bootstrap.
- `inventory-recovery.example.ini` describes current roles after promotion and
  remains the steady-state inventory for failover, backup and rebuild work.

Copy the relevant template to its ignored `.ini` name and edit the addresses.
Hostnames identify machines; inventory groups identify their current database
roles. The M13--M16 labels remain learning stages, not operational filenames.

## PostgreSQL standby and DR tool

M13 uses roles for host-specific database provisioning: `standby_preflight`,
`postgres_primary`, `postgres_standby` and `todo_dr`. Prepare and bootstrap the
two-host topology with the files documented in
[STANDBY-ARCHITECTURE.md](STANDBY-ARCHITECTURE.md) and
[STANDBY-BOOTSTRAP.md](STANDBY-BOOTSTRAP.md). Install or update only the local
DR tool
on an existing standby with:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory-initial.ini \
  ansible/install-dr-tool.yml
```

The promotion operation itself is intentionally local Python, not Ansible. Read
[PROMOTION.md](PROMOTION.md) before testing it.

## Promoted application and backup

M14 uses the `promoted_application` role to deploy the existing application
release only after PostgreSQL has been promoted and verified writable. Follow [APPLICATION-FAILOVER.md](APPLICATION-FAILOVER.md); it deliberately does
not bootstrap roles or
run migrations during an incident.

M15 uses the `postgres_backup` role to add a separate backup volume and
continuous WAL archiving to that promoted host. The local `todo_backup.py`
tool creates verified physical base backups and restores only into fixed,
disposable Podman resources. Follow
[BACKUP-PITR.md](BACKUP-PITR.md). The same-VM backup volume is a PITR
demonstration, not protection against loss of the host.

## Restore redundancy after failover

M16 uses `postgres_redundancy_primary` to preserve M15 archiving while exposing
a firewalled replication endpoint on the promoted host. The destructive
`postgres_reseed_standby` role then replaces only the explicitly confirmed old
primary volume with a fresh base backup. Follow
[RESTORE-REDUNDANCY.md](RESTORE-REDUNDANCY.md). This restores a second
database copy; it is not an automatic failback or switchover. After rebuilding,
keep a site-specific copy of
`inventory-recovery.example.ini` as the single role-based steady-state
inventory.
