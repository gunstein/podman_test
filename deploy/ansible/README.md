# Ansible DR and multi-host operations

Single-host installation and removal use the [Python installer](../installer/README.md).
Ansible retains remote transport, fencing, replication, backup, promotion,
standby rebuild, security integration and operational assertions.

Run playbooks from the repository or extracted operations-package root.
`ansible.cfg` pins `/usr/bin/python3`, sets the role search path and enables
pipelining. Install controller tooling on a connected development machine with:

```bash
python3 -m venv deploy/ansible/.venv
deploy/ansible/.venv/bin/python -m pip install -r deploy/ansible/requirements.txt
```

## Shared workload installer on targets

Each target needs Python 3.9+ and Jinja2, provisioned before offline operation
(for example, the OS `python3-jinja2` package). No target needs Helm or pip.
`tasks/install-workload.yml` stages the Python module, canonical Quadlet
sources and each caller's already-rendered manifests. Controller source paths
are preserved; remote commands receive target staging paths. The task sets
`PYTHONPATH` explicitly, so no editable pip installation is required.

On ordinary targets, staging is under the service user's
`~/.local/share/todo-installer`. When target `fapolicyd` is active, the existing
`todo_fapolicyd` role installs root-owned Python sources under
`/opt/todo/lib/todo_installer`, refreshes exact controller/target file trust,
and waits for the trust database. Supply the same controller/target become
credentials as other hardened DR operations (`--ask-become-pass` when needed).
Neither trust rules nor SELinux enforcement are relaxed.

The Python `install-workload` command returns one JSON line, `{"changed": true}`
or `{"changed": false}`. The task preserves `todo_postgres_kube_changed`,
`todo_application_kube_changed` and `todo_proxy_kube_changed`. Shared functions
install definitions and reload systemd; DR callers retain their existing
conditional restarts and safety ordering.

The four former shared/single-host runtime roles have been removed. The only
Quadlet template source remains `deploy/quadlet/*.kube.j2`.

## Single-host compatibility entry points

`playbooks/deploy.yml` and `playbooks/uninstall.yml` remain thin Python CLI
wrappers for existing automation. They do not contain installation logic and
conservatively report the command as changed. Their old variables are forwarded,
including deployment mode, bundle path, image refresh, publish address/port,
Quadlet directory and `remove_data`.

Use the Python CLI directly for the first interactive installation. Ansible
commands do not provide a TTY: missing bootstrap or Keycloak administrator
secrets fail clearly instead of silently accepting empty input. Automated
wrapper calls require those raw Podman secrets to exist already.

```bash
PYTHONPATH=deploy/installer python3 -m todo_installer install --mode server
PYTHONPATH=deploy/installer python3 -m todo_installer uninstall
# Only when permanently deleting the single-host database is intended:
PYTHONPATH=deploy/installer python3 -m todo_installer uninstall --remove-data
```

Normal uninstall preserves database and backup volumes and database/Keycloak
secrets; both modes remove proxy TLS state. The uninstaller refuses replication,
promotion, backup and rebuilt-standby hosts. Existing data requires its existing
credentials on reinstall. See [secrets](../../docs/SECRETS.md).

## Operations package and inventories

Build one source-only package for standby bootstrap, promotion, application
failover, backup and standby rebuild:

```bash
deploy/scripts/build-operations-package.sh
```

The package `VERSION` file records the source Git revision and whether source
changes were present while it was built. Deploy only a reviewed `clean`
artifact; `dirty` is diagnostic provenance, not a release identifier.

The package provides two inventory templates:

- `inventories/initial/hosts.example.ini` describes the original primary and standby
  before the first replication bootstrap.
- `inventories/recovery/hosts.example.ini` describes current roles after promotion and
  remains the steady-state inventory for failover, backup and rebuild work.

Copy the relevant template to `hosts.ini` in the same directory and edit the
addresses. The adjacent `group_vars/` contains account and operational defaults;
review these when adapting an inventory to real hosts. These are Ansible group
names, not Helm environment names.
Hostnames identify machines; inventory groups identify their current database
roles. Operational filenames describe actions and roles.

## Initial standby and DR tool

The standby bootstrap uses roles for host-specific database provisioning: `standby_preflight`,
`postgres_primary`, `postgres_standby` and `todo_dr`. Prepare and bootstrap the
two-host topology with the files documented in
[STANDBY-ARCHITECTURE.md](STANDBY-ARCHITECTURE.md) and
[STANDBY-BOOTSTRAP.md](STANDBY-BOOTSTRAP.md). Install or update only the local
DR tool
on an existing standby with:

```bash
deploy/ansible/.venv/bin/ansible-playbook --ask-become-pass \
  --inventory deploy/ansible/inventories/initial/hosts.ini \
  deploy/ansible/playbooks/install-dr-tool.yml
```

The promotion operation itself is intentionally local Python, not Ansible. Read
[PROMOTION.md](PROMOTION.md) before testing it.

## Promoted application and backup

The application failover uses the `promoted_application` role only after
PostgreSQL has been promoted and verified writable. It installs the grouped
`todo-app` and independent `todo-keycloak` Kube workloads directly.
Database roles are not bootstrapped during the incident; the app's normal
idempotent schema migration remains the init-container responsibility. Follow
[APPLICATION-FAILOVER.md](APPLICATION-FAILOVER.md).

Historical migration/rollback playbooks were retired after full acceptance of
688a0f6. They remain in Git history, not in the active operations package.

The backup workflow uses the `postgres_backup` role to add a separate backup volume and
continuous WAL archiving to that promoted host. The local `todo_backup.py`
tool creates verified physical base backups and restores only into fixed,
disposable Podman resources. Follow
[BACKUP-PITR.md](BACKUP-PITR.md). The same-VM backup volume is a PITR
demonstration, not protection against loss of the host.

## Restore redundancy after failover

The redundancy workflow uses `postgres_redundancy_primary` to preserve WAL archiving while exposing
a firewalled replication endpoint on the promoted host. The destructive
`postgres_reseed_standby` role then replaces only the explicitly confirmed old
primary volume with a fresh base backup. Follow
[RESTORE-REDUNDANCY.md](RESTORE-REDUNDANCY.md). This restores a second
database copy; it is not an automatic failback or switchover. After rebuilding,
keep a site-specific copy of
`inventories/recovery/hosts.example.ini` as the single role-based steady-state
inventory.

For full validation, follow [ACCEPTANCE.md](../../docs/ACCEPTANCE.md), which uses
direct DR tools and playbooks as the single normal execution path.
