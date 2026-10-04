# The standby is down, behind or lost its slot

**You notice it** when the DR check fails. On the primary: `no standby
streams from this primary over TLS`, or `replication slot ... is inactive,
losing WAL or invalidated`. On the standby: `the standby does not receive
WAL from the primary`. Users notice nothing: the primary still serves. But
there is no second copy until it is fixed.

## 1. Find out which case it is

On the primary, for each database (todo on 5432; `notes` and `keycloak` work
the same, with their own container):

```bash
podman exec todo-postgres psql -U todo -d postgres -c \
  "SELECT slot_name, active, wal_status, invalidation_reason FROM pg_replication_slots;"
```

- **Slot active or `reserved`/`extended`, just not streaming right now:** the
  standby is down or cannot connect. Start it, or fix its network or
  firewall (ports 5432-5434 from the standby to the primary). The primary
  keeps up to 1 GB of WAL per database for it (`max_slot_wal_keep_size`);
  when the standby comes back it catches up on its own, and the check passes
  again. Nothing else to do.
- **`wal_status` is `lost`, or `invalidation_reason` is set:** the standby was
  away too long and the WAL it needs is gone. It can never catch up: it must
  be copied again from scratch. Go on to 2.

## 2a. After a failover: the old primary becomes the standby

This is the tested case (ACCEPTANCE.md phase 9, every run). The old machine
must come back in quarantine, never with its network simply reconnected:
start it with every link down, stop its services with the Guest Agent
helper, allow only replication to the new primary, then, on the current
primary with `recovery.yaml` (see [primary-lost.md](primary-lost.md)):

```bash
cd ~/todo-operations && export PYTHONPATH="$PWD/deploy/dr" PYTHONDONTWRITEBYTECODE=1
python3 -m app_ops --inventory recovery.yaml preflight-standby-rebuild \
  --confirm-fenced 'todo-primary is fenced' --confirm-reseed todo-primary
python3 -m app_ops --inventory recovery.yaml rebuild-standby \
  --confirm-fenced 'todo-primary is fenced' --confirm-reseed todo-primary
```

Follow phase 9 of [ACCEPTANCE.md](../ACCEPTANCE.md) step by step; the
quarantine is in [PROXMOX-QUARANTINE.md](../PROXMOX-QUARANTINE.md).
`rebuild-standby` deletes the old machine's databases only after every check
of the whole group passed. **Never run it twice after a failure**: read its
message, fix the cause, and run the preflight again.

## 2b. Without a failover: a standby that lost its slot

**Not tested in acceptance**, and there is no single command for it yet
(backlog D10). The standby's copy is thrown away and taken again, as at
bootstrap:

1. On the standby, stop the three databases, then remove their data volumes
   (the standby's copy only; the primary keeps everything):
   `systemctl --user stop todo-postgres.service notes-postgres.service keycloak-postgres.service`,
   then `podman volume rm todo-postgres-data notes-postgres-data keycloak-postgres-data`.
2. On the primary, drop every slot that is lost or inactive, for each
   database, for example
   `podman exec todo-postgres psql -U todo -d postgres -c "SELECT pg_drop_replication_slot('todo_standby');"`.
3. From the operations package on the primary, with `initial.yaml` (the
   current primary as `primary`, this host as `standby`), run
   `preflight-standby` and then `bootstrap-standby`. Both refuse
   before anything changes if a step above is missing.
4. The DR check passes again on both hosts.

**Never** drop a slot that is `active`, and never remove a data volume on
the primary.
