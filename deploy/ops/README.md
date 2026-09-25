# app-ops: DR operations over plain SSH

`app-ops` runs the DR and multi-host operations over plain `ssh` from the
controller, without Ansible. Each command has the same name, host roles and
order as the Ansible playbook it replaces. The hosts still run the same
`app_installer` commands, `app_dr.py`, `app_backup.py` and
`deploy/scripts/trust-files.sh`. The Ansible playbooks remain supported until
a full two-VM acceptance run has verified `app-ops`.

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
