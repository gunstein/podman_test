# app-ops: DR operations over plain SSH

`app-ops` runs the DR and multi-host operations over plain `ssh` from the
controller. It is the only DR tool: the Ansible playbooks it replaced were
retired after its CLEAN PASS on `2165933`
([PROJECT.md](../../PROJECT.md#acceptance)); Git history keeps them. The hosts
run the same `app_installer` commands, `app_dr.py`, `app_backup.py` and
`deploy/scripts/trust-files.sh` that the playbooks ran.

These operations protect one group of three databases: Todo, Notes and the
shared Keycloak database (`apps.REPLICATED_DATABASES`). Each has its own host
replication port (5432, 5433, 5434), slot, credential, WAL archive and backup
volume. Every command refuses a partial group. Promoted application recovery
deploys both apps, Keycloak and the shared proxy.

Single-host installation and removal use the
[Python installer](../installer/README.md) directly, with no DR tool:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer install --mode server
PYTHONPATH=deploy/installer python3 -m app_installer uninstall
# Only when permanently deleting the single-host database is intended:
PYTHONPATH=deploy/installer python3 -m app_installer uninstall --remove-data
```

Both refuse a host with replication, promotion or backup state.

## Requirements

- Key-based SSH from the controller to every remote host, with the host key
  already in `known_hosts`. app-ops uses `BatchMode=yes` and
  `StrictHostKeyChecking=yes`, so it never prompts.
- Passwordless sudo (`NOPASSWD`) for the inventory user on every host,
  including the controller. app-ops runs privileged steps as `sudo -n` and
  never reads, sends or stores a password. A host that asks for one fails
  with a message saying so.
- Python 3 with PyYAML and Jinja2 on the controller, as the targets need.

On a controller where fapolicyd is active, app-ops is itself project Python.
Trust its files once before the first run, from the extracted operations
package:

```bash
sudo sh deploy/scripts/trust-files.sh trust todo \
  "$PWD"/deploy/ops/app_ops/*.py "$PWD"/deploy/installer/app_installer/*.py
```

After changing or replacing the package, run the same command again.

## Inventory

A small YAML file. The initial topology uses the roles `primary` and `standby`.
After promotion, the roles are `current_primary` and `rebuild_standby`.
Addresses must be literal IPv4 addresses. Mark the host you run on with
`local: true`.

```yaml
user: ops
# Optional; defaults to /home/<user> and <home>/todo-offline-m12.
home: /home/ops
bundle: /home/ops/todo-offline-m12
hosts:
  todo-primary: {role: primary, address: 192.0.2.10, local: true}
  todo-standby: {role: standby, address: 192.0.2.11}
```

## Commands

```bash
export PYTHONPATH="$PWD/deploy/ops" PYTHONDONTWRITEBYTECODE=1
python3 -m app_ops --inventory initial.yaml preflight-standby
python3 -m app_ops --inventory initial.yaml bootstrap-standby
python3 -m app_ops --inventory initial.yaml replication-status
python3 -m app_ops --inventory initial.yaml install-dr-tool
python3 -m app_ops --inventory initial.yaml install-quarantine-tool
python3 -m app_ops --inventory recovery.yaml deploy-promoted-application
python3 -m app_ops --inventory recovery.yaml configure-backup
python3 -m app_ops --inventory recovery.yaml rebuild-standby \
  --confirm-fenced "todo-primary is fenced" --confirm-reseed todo-primary
python3 -m app_ops --inventory recovery.yaml cluster-status
```

`sync-standby-secrets` and `preflight-standby-rebuild` can also be run on
their own. Every command prints one JSON result: `changed`, or the status
report. `deploy-promoted-application` must run on the promoted host itself,
marked `local: true`.

## Operations package

Build one source-only package with app-ops, the installer module, the host
tools and the rendered manifests:

```bash
deploy/scripts/build-operations-package.sh
```

Its `VERSION` file records the source Git revision and whether source changes
were present while it was built. Deploy only a reviewed `clean` artifact;
`dirty` is diagnostic provenance, not a release identifier.

## The workflows

Each page says what a command does, what it refuses and what evidence
acceptance records:

1. [Preparing the two hosts](STANDBY-ARCHITECTURE.md): accounts, SSH keys and
   the firewall rule.
2. [Standby bootstrap](STANDBY-BOOTSTRAP.md): `preflight-standby`,
   `bootstrap-standby`, `replication-status`.
3. [Promotion](PROMOTION.md): `install-dr-tool`, then the local `app_dr.py`.
4. [Application failover](APPLICATION-FAILOVER.md):
   `deploy-promoted-application`.
5. [Backup and PITR](BACKUP-PITR.md): `configure-backup`, then the local
   `app_backup.py`.
6. [Restoring redundancy](RESTORE-REDUNDANCY.md): `preflight-standby-rebuild`,
   `rebuild-standby`, `cluster-status`.

For full validation, follow [ACCEPTANCE.md](../../docs/ACCEPTANCE.md).

## Hosts installed before the tool rename

The operator tools were renamed from `todo_dr.py`, `todo_backup.py` and
`todo-quarantine.sh` to `app_dr.py`, `app_backup.py` and `app-quarantine.sh`.
Host state keeps its names (`/opt/todo`, `~/.config/todo`, volumes and the
`todo` fapolicyd trust file). On a host that already has the old files:

1. Run `install-dr-tool`, `configure-backup` or `install-quarantine-tool`
   again so the new names are installed and trusted.
2. Point hypervisor-side quarantine calls at `/opt/todo/bin/app-quarantine.sh`.
   With `--enable-selinux-entrypoint`, remove the old file context with
   `sudo semanage fcontext -d '/opt/todo/bin/todo-quarantine\.sh'`.
3. Remove the old copies and their trust entries:

   ```bash
   cd /opt/todo/bin
   sudo fapolicyd-cli --file delete /opt/todo/bin/todo_dr.py --trust-file todo
   sudo fapolicyd-cli --file delete /opt/todo/bin/todo_backup.py --trust-file todo
   sudo fapolicyd-cli --file delete /opt/todo/bin/todo-quarantine.sh --trust-file todo
   sudo fapolicyd-cli --update
   sudo rm -f todo_dr.py todo_backup.py todo-quarantine.sh
   ```

`uninstall` still treats the old file names as clustered state and refuses.
