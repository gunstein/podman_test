# Physical backup, WAL archive and PITR

This is learning stage M15.

M15 demonstrates why replication is not backup. It enables continuous WAL
archiving, creates a verified physical base backup and restores to a named point
in time inside an isolated disposable container.

The archive is used for backup and PITR, not as a WAL source for the M13/M16
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

Keep the old primary fenced. On the trusted source host, build, verify and
transfer the package:

```bash
scripts/build-operations-package.sh
(
  cd dist
  sha256sum -c todo-operations.tar.gz.sha256
)
read -rp "Promoted host IPv4 address: " TODO_PROMOTED_IP
scp \
  dist/todo-operations.tar.gz \
  dist/todo-operations.tar.gz.sha256 \
  "gunstein@${TODO_PROMOTED_IP}:"
```

On the promoted host, verify and extract both files:

```bash
cd "$HOME"
sha256sum -c todo-operations.tar.gz.sha256
mkdir -p todo-operations
tar -xzf todo-operations.tar.gz \
  --strip-components=1 \
  --directory todo-operations
cd todo-operations
```

On Oracle Linux with active `fapolicyd`, trust the verified extracted Python
source:

```bash
sudo fapolicyd-cli --file add \
  "$HOME/todo-operations/scripts/todo_backup.py" \
  --trust-file todo-backup-source
sudo fapolicyd-cli --update
```

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
the previous 60-second setting could consume about 23 GiB per day under even
small recurring writes. One hour limits that worst-case time-driven growth to
about 384 MiB per day. The `mark` command and the Ansible verification still
force an explicit WAL switch, so drills do not need an aggressive timeout.

The archive remains intentionally non-circular: PostgreSQL must never silently
discard WAL that belongs to the retained recovery window. After creating and
verifying a replacement base backup, an operator must explicitly expire older
base backups and WAL, or copy them to off-host storage. Monitoring free space is
still required.

Trust the exact installed tool before running it. On a hardened `fapolicyd`
host, this trust step is also required before a repeat playbook run can read the
destination and report `changed=0`:

```bash
sudo fapolicyd-cli --file add \
  "$HOME/.config/todo/todo_backup.py" \
  --trust-file todo-backup
sudo fapolicyd-cli --update
```

After an update, refresh both registered copies and reload the trust database:

```bash
sudo fapolicyd-cli --file update \
  "$HOME/todo-operations/scripts/todo_backup.py" \
  --trust-file todo-backup-source
sudo fapolicyd-cli --file update \
  "$HOME/.config/todo/todo_backup.py" \
  --trust-file todo-backup
sudo fapolicyd-cli --update
```

See [../offline/FAPOLICYD.md](../offline/FAPOLICYD.md) for denial
diagnostics, common Ansible symptoms and trust-entry cleanup. Do not disable `fapolicyd` for this demo.

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

The command returns only after the exact WAL segment containing the restore
point is present in the archive. It does not rely on that segment still being
reported as the most recently archived one.

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
If recovery fails, disposable state may remain for inspection. After diagnosing
it, rerun only with explicit replacement:

```bash
python3 "$HOME/.config/todo/todo_backup.py" restore \
  --backup base-YYYYMMDDTHHMMSSZ \
  --target m15_before_after \
  --replace
```

`--replace` can delete only the fixed disposable restore container and volume;
it never targets the live or backup volume.

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

The live Oracle Linux 9.8 drill used promoted host `192.168.0.109` and created
and verified `base-20260829T102943Z`. It archived a named restore point and
restored it into the isolated disposable container. The restored database
contained the before-target row and excluded the after-target row, while the
live database retained both. Cleanup removed only disposable restore state.
After a full VM reboot, PostgreSQL remained writable with archiving enabled,
the backup persisted, all application services recovered, and a repeat
playbook run completed with `changed=0`.

## Operational follow-up

A usable backup policy also needs:

- transfer to storage outside this VM;
- retention and capacity monitoring;
- alerts for `pg_stat_archiver.failed_count` and archive lag;
- protected copies of relevant configuration and secrets;
- regular automated restore verification;
- an explicit decision about backup encryption.
