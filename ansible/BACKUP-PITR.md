# Physical backup, WAL archive and PITR


The backup/PITR drill demonstrates why replication is not backup. It enables continuous WAL
archiving, creates a verified physical base backup and restores to a named point
in time inside an isolated disposable container.

The archive is used for backup and PITR, not as a WAL source for the initial or rebuilt
streaming standby. PostgreSQL can also use an archive through standby
`restore_command` to bridge WAL gaps, but that additional availability pattern
is documented rather than implemented in this deliberately small demo.

The live `todo-postgres` container and `todo-postgres-data` volume are never
restore targets.

## Storage scope

The demo uses a separate rootless Podman volume:

```text
todo-postgres-backup
├── base/
└── wal/
```

This protects recovery material from logical errors or deletion of the live
PostgreSQL volume. It does not protect against loss of the VM, its filesystem or
the physical host. A real deployment must copy this material to separately
administered storage and define retention, encryption and restore testing.

PostgreSQL documents that PITR requires a usable base backup plus an unbroken
sequence of archived WAL beginning no later than that backup. WAL contains
database contents and must be protected like the database itself.

## Install and configure

Use the verified artifacts and inventory prepared in
[Acceptance](../docs/ACCEPTANCE.md#2-build-and-stage-artifacts). Stage both
packages on both hosts before an incident; verify checksums and matching clean
VERSION values before running extracted code.

The configure playbook uses the central `todo_fapolicyd` role to refresh
exact source trust, install root-owned `/opt/todo/bin/todo_backup.py`, and
maintain its exact target trust entry. Supply normal Ansible become credentials;
do not disable `fapolicyd` or trust the extracted directory.

Reuse the recovery inventory created for application failover, or copy the
included example:

```bash
read -rp "Promoted host IPv4 address: " TODO_PROMOTED_IP
cp ansible/inventory-recovery.example.ini ansible/inventory-recovery.ini
sed -i "s/192.0.2.11/${TODO_PROMOTED_IP}/" ansible/inventory-recovery.ini
```

Then:

```bash
ansible-playbook \
  --ask-become-pass \
  --inventory ansible/inventory-recovery.ini \
  ansible/configure-backup.yml
```

The playbook refuses to continue unless the live database reports `f|off`. It
creates the separate backup volume, mounts it into PostgreSQL, enables
`archive_mode=on`, configures a non-overwriting `archive_command`, restarts
PostgreSQL once when required, restores the application tier and verifies that a
forced WAL segment reaches the archive.

The demo defaults to `archive_timeout=1h`. PostgreSQL archives complete 16 MiB
segments even when a forced early segment switch contains little useful WAL, so
one hour limits worst-case time-driven growth from recurring writes to
about 384 MiB per day. The `mark` command and the Ansible verification still
force an explicit WAL switch, so drills do not need an aggressive timeout.

The archive remains intentionally non-circular: PostgreSQL must never silently
discard WAL that belongs to the retained recovery window. After creating and
verifying a replacement base backup, an operator must explicitly expire older
base backups and WAL, or copy them to off-host storage. Monitoring free space is
still required.

The playbook verifies the exact installed trust entry before it returns. See
[../offline/FAPOLICYD.md](../offline/FAPOLICYD.md) for separate SELinux and
fapolicyd diagnostics and trust-entry cleanup.

## Tool contract

| Command | Contract |
|---|---|
| `status` | Reports live role and archive diagnostics |
| `create` | Requires writable database and archive mode; streams a base backup and verifies its SHA-256 manifest with `pg_verifybackup` |
| `mark --name NAME` | Creates a named restore point, switches WAL and waits for the exact segment in the archive |
| `restore --backup NAME --target POINT` | Copies into fixed disposable resources and pauses recovery at the target; database networking is disabled and backup is mounted read-only |
| `restore-status` | Reports recovery, pause and read-only state |
| `cleanup-restore --confirm todo-postgres-restore` | Deletes only the fixed disposable restore container and volume |

The live data volume is never a restore target. Existing disposable state causes
restore to stop; inspect it before explicitly authorizing `--replace`, which can
remove only the disposable resources. A failed restore may leave a volume for
inspection. Follow [troubleshooting](../docs/ACCEPTANCE-TROUBLESHOOTING.md) for
failures and [the PITR phase](../docs/ACCEPTANCE.md#8-backup-and-isolated-pitr)
for the normal before/after comparison and approved cleanup sequence.

## Acceptance evidence

Use [Acceptance](../docs/ACCEPTANCE.md) for the full sequence and verdict.
[688a0f6](../docs/ACCEPTANCE-688a0f6.md) records historical evidence only.

## Operational follow-up

A usable backup policy also needs:

- transfer to storage outside this VM;
- retention and capacity monitoring;
- alerts for `pg_stat_archiver.failed_count` and archive lag;
- protected copies of relevant configuration and secrets;
- regular automated restore verification;
- an explicit decision about backup encryption.
