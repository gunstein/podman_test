# Controlled PostgreSQL promotion

The operations package installs a small Python tool on standby. Ansible provisions the file and
host-specific configuration, but status, preflight and promotion run locally on
standby. A disaster operation therefore does not depend on primary or SSH.

The operational targets for this demo are:

- RTO: restore database write availability within 15 minutes.
- RPO: target at most 30 seconds of data loss during healthy asynchronous
  replication.

The RPO is an operational target, not a guaranteed upper bound. After primary
is lost, standby can measure received-but-unreplayed WAL, but it cannot detect
transactions that committed on primary and were never transmitted. Regular
monitoring of streaming state is therefore part of the RPO assumption.
The local `status` command labels this configured RPO target as informational;
it does not claim to enforce or prove that bound after primary loss.

## Install before an incident

Use the verified artifacts and inventory prepared in
[Acceptance](../docs/ACCEPTANCE.md#2-build-and-stage-artifacts). Stage both
packages on both hosts before an incident; verify checksums and matching clean
VERSION values before running extracted code.

The installer uses the central `todo_fapolicyd` role. With normal Ansible
become credentials it refreshes exact source-file trust on the controller,
installs root-owned `/opt/todo/bin/todo_dr.py` on the standby, registers only
that exact target file, and reloads the policy. It never trusts the operations
directory or disables `fapolicyd`.

Run this from primary while primary is still the Ansible controller:

```bash
ansible-playbook --ask-become-pass \
  --inventory ansible/inventory-initial.ini \
  ansible/install-dr-tool.yml
```

The non-secret DR configuration remains
`~/.config/todo/todo-dr.json`. See
[../offline/FAPOLICYD.md](../offline/FAPOLICYD.md) for denial diagnosis and
exact-file cleanup.

## Safe status test

On standby, while primary is healthy:

```bash
python3 /opt/todo/bin/todo_dr.py status
```

Expected output includes `Database role: standby`, `Writable: no`, zero local
apply lag and `Primary endpoint ...: reachable`. Local apply lag only compares
WAL already received by standby; it is not the possible data loss on primary.

Do not expect `preflight` to pass during normal operation. It deliberately
rejects a reachable primary.

## Promotion contract

Follow [the fencing and promotion phase](../docs/ACCEPTANCE.md#6-fence-and-promote)
for normal execution. Promotion changes topology and is not a routine health test.

`preflight` requires an exact fencing assertion, the configured local hostname,
active service, healthy container, read-only recovery state, available receive
and replay LSNs, zero local apply lag and an unreachable old-primary TCP5432.
An unreachable endpoint supports the operator's decision; it cannot prove
infrastructure fencing. Power off the old primary, prevent automatic restart
and verify that its service IP is not assigned elsewhere.

`promote` repeats all preflight checks, requires the exact standby hostname as
confirmation, runs `pg_ctl promote` and verifies `f|off`. Keep old primary fenced;
it can only rejoin after explicitly approved standby rebuild. Application
recovery is a separate operation. If promotion is interrupted, obtain fresh
local role and fencing evidence before any further action; never retry blindly.

## Acceptance evidence

Use [Acceptance](../docs/ACCEPTANCE.md) for the full sequence and verdict.
[688a0f6](../docs/ACCEPTANCE-688a0f6.md) records historical evidence only.
