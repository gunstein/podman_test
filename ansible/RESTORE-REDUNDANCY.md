# Restore database redundancy after failover


Standby rebuild restores a second database copy after database promotion and application
failover. It does not move service back to the machine that was originally
primary. The promoted host remains primary; the old primary is destroyed and
re-seeded as a new read-only standby.

```text
todo-standby (current primary, app, backup)
        │
        │ pg_basebackup + async WAL streaming
        ▼
todo-primary (rebuilt standby, database only)
```

Promotion restored availability. This procedure restores redundancy.

## Safety boundary

The old primary contains a divergent writable database and must never be allowed
to serve clients or replicate as primary. Keep it fenced at the Proxmox layer.
Powering it on is safe only with client/database traffic still blocked and for
the purpose of stopping its services and rebuilding it.

The rebuild playbook permanently deletes `todo-postgres-data` on the old primary.
It requires both exact values:

```text
todo-primary is fenced
todo-primary
```

A Proxmox snapshot may be retained for investigation, but it must not later be
booted onto the production network as a writable database.

## Quarantine the old primary

Use the rehearsed [Proxmox quarantine procedure](../docs/PROXMOX-QUARANTINE.md).
Boot with all links disconnected, run the labelled stop helper through Guest
Agent, require completed exitcode=0 and STOPPED, inspect applied IPv4/IPv6 rules,
and only then reconnect restricted SSH. Keep quarantine through verification.
If Guest Agent preparation fails, consult
[troubleshooting](../docs/ACCEPTANCE-TROUBLESHOOTING.md); do not improvise access.

## Staged operations package

Use the verified artifacts and inventory prepared in
[Acceptance](../docs/ACCEPTANCE.md#2-build-and-stage-artifacts). Stage both
packages on both hosts before an incident; verify checksums and matching clean
VERSION values before running extracted code.

## Rebuild contract

Use [the rebuild phase](../docs/ACCEPTANCE.md#9-rebuild-old-primary-as-standby)
for commands and firewall preparation. The current primary controls Ansible;
`inventory-recovery.ini` states the reversed roles. The initial inventory is
only for initial bootstrap. Hostnames continue to identify the same machines.

Preflight requires one current primary and one rebuild target with distinct
addresses, expected host identities, a writable current primary, replication
role and secrets, identical replication credentials on both hosts, an existing
backup volume, an absent new slot, existing old data, stopped old services and
both exact operator confirmations. Secret values remain under `no_log`.

The rebuild playbook imports preflight again. It preserves archiving while
publishing a narrowly firewalled replication endpoint. Before deleting old data,
the target requires its PostgreSQL image, TCP connectivity and authenticated
physical replication via `IDENTIFY_SYSTEM`. TCP connectivity alone is insufficient.
Rootless port forwarding hides the original peer address from PostgreSQL;
firewalld and hypervisor quarantine enforce the real machine boundary.

Only then is the old data volume removed, a replacement created, a physical slot
created and a base backup streamed. Recovery settings and the private replication
passfile live inside the volume. Application Kube units are removed from the
rebuilt host; only PostgreSQL returns there. No app-role bootstrap is part of this
operation. Replication remains SCRAM-authenticated on the trusted lab LAN.

This is one-shot work. An absent slot alone does not prove rebuild never started:
a failure can occur after volume replacement and before slot creation. Stop on
any partial failure, inspect both roles, slot, volume, logs and quarantine, and
review recovery explicitly. Never repeat destructive rebuild to finish a failed
DR-tool installation; use [troubleshooting](../docs/ACCEPTANCE-TROUBLESHOOTING.md).

`cluster-status.yml` checks writable primary, active usable streaming slot,
read-only recovery standby and archive health after its latest failure. Read the
reported lag and LSNs too; its exit code does not prove zero lag. Full acceptance
also requires a fresh authenticated Todo on rebuilt standby and sequential
standby-then-primary reboots with application, CA, data and backup persistence.

## Acceptance evidence

Use [Acceptance](../docs/ACCEPTANCE.md) for the full sequence and verdict.
[688a0f6](../docs/ACCEPTANCE-688a0f6.md) records historical evidence only.

## Failback is separate

The healthy end state may remain:

```text
todo-standby = primary
todo-primary = standby
```

Returning primary service to `todo-primary` would be a planned switchover with
its own fencing, catch-up, service-address and rollback procedure. This rebuild workflow does not
automatically perform that separate operation.
