# Resumable DR drill

`scripts/todo_dr_run.py` removes repeated command entry from the destructive
two-host DR drill. It deliberately does not decide that a host is fenced,
change hypervisor power state, open firewall rules or alter the client DNS
mapping. Those remain infrastructure and operator boundaries.

The runner executes one stage at a time and records completion in
`~/.config/todo/todo-dr-run.json`. The file contains stage names, timestamps
and the inventory path, but no credentials. Copy the verified operations
package and prepare the role-based recovery inventory before an incident.

On a host with active `fapolicyd`, trust only the verified runner file before
Python reads it from the extracted operations package:

```bash
sudo fapolicyd-cli --file add \
  "$HOME/todo-operations/scripts/todo_dr_run.py" \
  --trust-file todo-dr-run-source
sudo fapolicyd-cli --update
```

## 1. Fence and promote

Power off and independently verify the old primary as described in
[PROMOTION.md](PROMOTION.md). On the standby, now the prospective primary:

```bash
cd "$HOME/todo-operations"

python3 scripts/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  promote \
  --confirm-primary-fenced 'todo-primary is fenced' \
  --confirm-promotion todo-standby
```

The runner performs a read-only local preflight before recording the promotion
stage. The promotion tool repeats the same checks immediately before
`pg_ctl promote`. If the destructive stage starts but does not complete, the
runner refuses to retry it automatically because PostgreSQL may already have
changed role.

## 2. Restore the application

Prepare the firewall and stable client mapping from
[APPLICATION-FAILOVER.md](APPLICATION-FAILOVER.md), then run:

```bash
python3 scripts/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  deploy-application
```

This first restores the proven per-container application runtime. It gives the
incident path the same small and already accepted availability boundary as the
manual procedure.

## 3. Quarantine and rebuild the old primary

Boot the old primary only inside the quarantine boundary, stop every Todo
service, allow its management connection and add only the restricted
replication firewall rule described in
[RESTORE-REDUNDANCY.md](RESTORE-REDUNDANCY.md). Run the read-only check as often
as needed:

```bash
python3 scripts/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  rebuild-preflight \
  --confirm-old-primary-fenced 'todo-primary is fenced' \
  --confirm-reseed todo-primary
```

After reviewing its output, authorize the one-shot destructive rebuild:

```bash
python3 scripts/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  rebuild \
  --confirm-old-primary-fenced 'todo-primary is fenced' \
  --confirm-reseed todo-primary
```

The rebuild command reruns preflight before it records the destructive stage.
Once that stage is recorded as started, it is never retried automatically.
Follow the partial-rebuild diagnostics in `RESTORE-REDUNDANCY.md` if it fails.

## 4. Return the current primary to Kube and verify

After streaming replication and WAL archiving have returned, migrate the
current primary back to the accepted four-pod Kube runtime:

```bash
python3 scripts/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  migrate-kube

python3 scripts/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  verify
```

`migrate-kube` migrates PostgreSQL first, while the caught-up rebuilt standby
provides the required safety boundary, and then migrates backend, Keycloak and
nginx. `verify` runs the existing replication and WAL archive assertions.

Show resumable progress at any time:

```bash
python3 scripts/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  status
```

Reboot checks, the external HTTPS/browser test and the final firewall review
remain explicit acceptance steps. They cross machine or client trust
boundaries and should not be hidden inside the database mutation command.
