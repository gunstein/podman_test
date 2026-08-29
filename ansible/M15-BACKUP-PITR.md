# M15 physical backup, WAL archive and PITR

M15 demonstrates why replication is not backup. It enables continuous WAL
archiving, creates a verified physical base backup and restores to a named point
in time inside an isolated disposable container.

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

Keep the old primary fenced. Build and checksum the M15 package on the trusted
source host, then copy both files to the promoted host.

On Oracle Linux with active `fapolicyd`, verify the package checksum before
trusting the extracted Python source:

```bash
sudo fapolicyd-cli --file add \
  "$HOME/todo-m15-test/scripts/todo_backup.py" \
  --trust-file todo-backup-source
sudo fapolicyd-cli --update
```

Reuse the promoted-host inventory from M14 or copy the included example:

```bash
cp ansible/inventory-m14.example.ini ansible/inventory-m15.ini
sed -i 's/192.0.2.11/192.168.0.109/' ansible/inventory-m15.ini
```

Then:

```bash
ansible-playbook \
  --inventory ansible/inventory-m15.ini \
  ansible/configure-backup-m15.yml
```

The playbook refuses to continue unless the live database reports `f|off`. It
creates the separate backup volume, mounts it into PostgreSQL, enables
`archive_mode=on`, configures a non-overwriting `archive_command`, restarts
PostgreSQL once when required, restores the application tier and verifies that a
forced WAL segment reaches the archive.

Trust the exact installed tool before running it. On a hardened `fapolicyd`
host, this trust step is also required before a repeat playbook run can read the
destination and report `changed=0`:

```bash
sudo fapolicyd-cli --file add \
  "$HOME/.config/todo/todo_backup.py" \
  --trust-file todo-backup
sudo fapolicyd-cli --update
```

After an update, use `--file update` for both registered paths.

## Status and base backup

```bash
python3 "$HOME/.config/todo/todo_backup.py" status

python3 "$HOME/.config/todo/todo_backup.py" create
```

The create command runs `pg_basebackup --wal-method=stream`, generates SHA-256
manifest checksums and requires `pg_verifybackup` to succeed. Record the
reported name, for example `base-20260829T123456Z`.

## Disposable PITR drill

Create data after the base backup but before the recovery target:

```bash
podman exec todo-postgres \
  psql --username todo --dbname todo \
  --set ON_ERROR_STOP=1 \
  --command "
    INSERT INTO todos (title, completed)
    VALUES ('M15 before restore point', false);
  "
```

Create and archive a named restore point:

```bash
python3 "$HOME/.config/todo/todo_backup.py" mark \
  --name m15_before_after
```

Then create data that must not exist in the restored view:

```bash
podman exec todo-postgres \
  psql --username todo --dbname todo \
  --set ON_ERROR_STOP=1 \
  --command "
    INSERT INTO todos (title, completed)
    VALUES ('M15 after restore point', false);
  "
```

Restore with the base-backup name returned earlier:

```bash
python3 "$HOME/.config/todo/todo_backup.py" restore \
  --backup base-YYYYMMDDTHHMMSSZ \
  --target m15_before_after
```

The tool copies into the fixed `todo-postgres-restore-data` volume and starts
the fixed `todo-postgres-restore` container with networking disabled. Recovery
pauses at the named point.

Verify the isolated database:

```bash
python3 "$HOME/.config/todo/todo_backup.py" restore-status

podman exec todo-postgres-restore \
  psql --username todo --dbname todo \
  --command "
    SELECT title
    FROM todos
    WHERE title LIKE 'M15 % restore point'
    ORDER BY id;
  "
```

Expected restored result contains only `M15 before restore point`. The live
`todo-postgres` database still contains both rows.

## Cleanup only the disposable restore

```bash
python3 "$HOME/.config/todo/todo_backup.py" cleanup-restore \
  --confirm todo-postgres-restore
```

The exact confirmation is required. Cleanup never addresses
`todo-postgres-data` or `todo-postgres-backup`.

## Verified Oracle Linux drill

The live Oracle Linux 9.8 drill created and verified
`base-20260829T102943Z`, archived a named restore point, and restored it into
the isolated disposable container. The restored database contained the
before-target row and excluded the after-target row, while the live database
retained both. Cleanup removed only disposable restore state. After a full VM
reboot, PostgreSQL remained writable with archiving enabled, the backup
persisted, all application services recovered, and a repeat playbook run
completed with `changed=0`.

## Operational follow-up

A usable backup policy also needs:

- transfer to storage outside this VM;
- retention and capacity monitoring;
- alerts for `pg_stat_archiver.failed_count` and archive lag;
- protected copies of relevant configuration and secrets;
- regular automated restore verification;
- an explicit decision about backup encryption.
