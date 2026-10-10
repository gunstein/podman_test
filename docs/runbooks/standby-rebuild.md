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
cd ~/platform-operations && export PYTHONPATH="$PWD/deploy/dr" PYTHONDONTWRITEBYTECODE=1
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

The standby's copy is thrown away and taken again while the primary keeps
serving (ACCEPTANCE.md phase 9 tests it on the rebuilt standby). If the
standby's databases are down, start them first (`systemctl --user start
todo-postgres.service notes-postgres.service keycloak-postgres.service`):
they come up as standbys and wait. Then, on the primary, with an inventory
naming the current primary as `primary` and the standby as `standby`
(`initial.yaml`):

```bash
cd ~/platform-operations && export PYTHONPATH="$PWD/deploy/dr" PYTHONDONTWRITEBYTECODE=1
python3 -m app_ops --inventory initial.yaml reseed-standby --confirm-reseed todo-standby
```

`--confirm-reseed` is the standby's name (here the standby is `todo-standby`;
after a failover it is `todo-primary`). It deletes the standby's three
databases only after each proved it is a read-only standby that reaches the
primary, and drops only idle slots on the primary. It prints
`{"changed": true}` when all three stream again; the DR check then passes on
both hosts. If it stops with `nothing was changed`, fix what it names and run
it again. If it stops later, the primary is untouched, and the standby is
finished by hand, as at bootstrap:

1. On the standby, remove any data volume that is left
   (`podman volume rm todo-postgres-data notes-postgres-data keycloak-postgres-data`;
   the standby's copy only, never on the primary).
2. On the primary, drop each slot that is not `active` (section 1 shows them),
   for example
   `podman exec todo-postgres psql -U todo -d postgres -c "SELECT pg_drop_replication_slot('todo_standby');"`.
3. With the same inventory, run `preflight-standby` and then
   `bootstrap-standby`. Both refuse before anything changes if a step above
   is missing.

**Never** drop a slot that is `active`, and never remove a data volume on
the primary.
