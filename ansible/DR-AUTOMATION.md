# Resumable DR drill

`/opt/todo/bin/todo_dr_run.py` removes repeated command entry from the
destructive two-host DR drill. It deliberately does not decide that a host is
fenced, change hypervisor power state, open firewall rules or alter the client
DNS mapping. Those remain infrastructure and operator boundaries.

The runner executes one stage at a time and records completion in
`~/.config/todo/todo-dr-run.json`. The file contains stage names, timestamps
and the inventory path, but no credentials. `install-dr-tool.yml` installs
both DR tools under `/opt/todo/bin` and maintains exact-file `fapolicyd`
trust through the central `todo_fapolicyd` role. Run it with Ansible become
credentials; no manual source or target trust commands are required.

When run from the installed path, the runner defaults to
`$HOME/todo-operations`. A source checkout is detected automatically. Every
Ansible stage validates its playbook before recording stage execution; use
`--project-root` only when the verified operations package was extracted
elsewhere.

## 1. Fence and promote

Power off and independently verify the old primary as described in
[PROMOTION.md](PROMOTION.md). On the standby, now the prospective primary:

```bash
cd "$HOME/todo-operations"

python3 /opt/todo/bin/todo_dr_run.py \
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

## 2. Restore the grouped application

Prepare the firewall and stable client mapping from
[APPLICATION-FAILOVER.md](APPLICATION-FAILOVER.md), then run:

```bash
python3 /opt/todo/bin/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  deploy-application
```

This installs `todo-app` and `todo-keycloak` directly as Kube workloads.
The existing promoted `todo-postgres` Kube workload remains in place.
Schema migration runs only as the `todo-app` init container.

## 3. Quarantine and rebuild the old primary

Boot the old primary only inside the quarantine boundary, stop every Todo
service, allow its management connection and add only the restricted
replication firewall rule described in
[RESTORE-REDUNDANCY.md](RESTORE-REDUNDANCY.md). Run the read-only check as often
as needed:

```bash
python3 /opt/todo/bin/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  rebuild-preflight \
  --confirm-old-primary-fenced 'todo-primary is fenced' \
  --confirm-reseed todo-primary
```

After reviewing its output, authorize the one-shot destructive rebuild:

```bash
python3 /opt/todo/bin/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  --ask-become-pass \
  rebuild \
  --confirm-old-primary-fenced 'todo-primary is fenced' \
  --confirm-reseed todo-primary
```

The rebuild command reruns preflight before it records the destructive stage.
Once that stage is recorded as started, it is never retried automatically.
The rebuilt host starts the same canonical `todo-postgres` Kube workload as a
standby; its recovery settings and passfile live in the database volume.

## 4. Verify the final Kube topology

Clean deploy, promotion, backup and rebuild are already Kube-native. There is
therefore no normal `migrate-kube` stage. Run:

```bash
python3 /opt/todo/bin/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  verify
```

`verify` requires completed promotion, application restore and standby
rebuild, then runs the existing replication and WAL archive assertions.
The old migration/rollback playbooks remain isolated transition evidence until
the full Kube acceptance retirement gate is complete; the runner never calls
them.

Show resumable progress at any time:

```bash
python3 /opt/todo/bin/todo_dr_run.py \
  --inventory ansible/inventory-recovery.ini \
  status
```

Reboot checks, external HTTPS/browser testing and the final firewall review
remain explicit acceptance steps. They cross machine or client trust
boundaries and must not be hidden inside a database mutation command.
