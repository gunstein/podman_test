# Backup and PITR on the promoted VM

This demonstrates a base backup plus WAL replay into a separate, read-only
restore container. Build on the laptop; run the remaining commands as the
service user on the current primary. Require sufficient free disk, known current
roles, and no existing disposable restore state. Configuration can restart the
application, so obtain a maintenance window on an existing installation.
Status and SELECT are observations; marker rows are lab writes. Never substitute
a live volume as the restore target. If backup verification, archiving or restore
fails, stop and use [backup/PITR](../../deploy/ops/BACKUP-PITR.md) and
[troubleshooting](../ACCEPTANCE-TROUBLESHOOTING.md).

This builds on [the two-VM DR walkthrough](03-DR-TWO-VM.md): run it on VM2
after promotion and application failover. Backup configuration refuses a host
without a completed group promotion, because only the current primary archives
WAL.

The goal is simple:

```text
Todo is running
   v
take a physical PostgreSQL backup
   v
create data
   v
create a restore point
   v
create more data
   v
restore to the restore point
   v
verify the result
```

The repository restores into an isolated, separate PostgreSQL container. The
live database is never overwritten.

## 1–3. Use VM2 from recipe 3

VM2 already has the operations package, trusts app-ops and has
`recovery.yaml` (recipe 3, steps 5 and 16), and the run-only passwordless sudo
from recipe 3 step 7 is still in place. On VM2:

```bash
cd ~/todo-operations
export PYTHONPATH="$PWD/deploy/ops" PYTHONDONTWRITEBYTECODE=1
cat recovery.yaml
```

## 4. Install and configure the backup feature

Run:

```bash
python3 -m app_ops --inventory recovery.yaml configure-backup
```

This installs:

```text
/opt/todo/bin/app_backup.py
```

and creates a separate Podman volume:

```text
todo-postgres-backup
├── base/
└── wal/
```

It enables PostgreSQL WAL archiving and checks that WAL actually
lands in the backup volume.

## 5. Check backup status

```bash
python3 /opt/todo/bin/app_backup.py status
```

Without `--app`, `status`, `create` and `mark` act on all three databases
(todo, notes, keycloak) and prefix each line with the database name. This
recipe restores only Todo; the same commands with `--app notes` restore Notes.

Check the volumes too:

```bash
podman volume ls | grep -- '-postgres-'
```

You should see a data and a backup volume per database, including:

```text
todo-postgres-data
todo-postgres-backup
```

## 6. Take a base backup

```bash
python3 /opt/todo/bin/app_backup.py create
```

The tool uses `pg_basebackup`, creates a SHA-256 manifest and runs
`pg_verifybackup`.

You get back one backup name per database, for example:

```text
todo: Verified base backup: base-20260907T081500Z
```

Keep the `todo:` name. You need it for the restore.

## Now build a PITR test

## 7. Create data that should exist after the restore

You can do this through the Todo application by creating a Todo named:

```text
Before restore point
```

Or directly in the database:

```bash
podman exec todo-postgres \
  psql --username todo --dbname todo \
  --set ON_ERROR_STOP=1 \
  --command "
    INSERT INTO todos (title, completed)
    VALUES ('Before restore point', false);
  "
```

## 8. Create a restore point

Run:

```bash
python3 /opt/todo/bin/app_backup.py mark \
  --name before_bad_change
```

This creates a named PostgreSQL restore point and ensures the WAL segment
containing it has safely reached the WAL archive.

You now have:

```text
base backup
     |
     +---- WAL ----> "Before restore point"
                      |
                      +-- restore point:
                          before_bad_change
```

## 9. Create data that should NOT exist after the restore

Create another Todo:

```text
After restore point
```

or:

```bash
podman exec todo-postgres \
  psql --username todo --dbname todo \
  --set ON_ERROR_STOP=1 \
  --command "
    INSERT INTO todos (title, completed)
    VALUES ('After restore point', false);
  "
```

The live database now contains both:

```text
Before restore point
After restore point
```

## 10. Restore the database to the restore point

Use the backup name from step 6:

```bash
python3 /opt/todo/bin/app_backup.py --app todo restore \
  --backup base-20260907T081500Z \
  --target before_bad_change
```

Substitute the backup name you actually got.

This does not replace the live database. Instead, the tool creates:

```text
todo-postgres-restore-data
todo-postgres-restore
```

The restore container has networking disabled and is isolated from the
production instance.

## 11. Check the restore

First:

```bash
python3 /opt/todo/bin/app_backup.py --app todo restore-status
```

Require `recovery|paused|read_only = t|t|on` and verify networking is disabled:

```bash
podman inspect todo-postgres-restore --format '{{.HostConfig.NetworkMode}}'
```

Expected: `none`. Then:

```bash
podman exec todo-postgres-restore \
  psql --username todo --dbname todo \
  --command "
    SELECT title
    FROM todos
    WHERE title IN ('Before restore point', 'After restore point')
    ORDER BY id;
  "
```

Expected result from the restore instance:

```text
Before restore point
```

but not:

```text
After restore point
```

That shows the database has been reconstructed to exactly the point you
chose.

## 12. Compare with the live database

The live database should still be untouched:

```bash
podman exec todo-postgres \
  psql --username todo --dbname todo \
  --command "
    SELECT title
    FROM todos
    WHERE title IN ('Before restore point', 'After restore point')
    ORDER BY id;
  "
```

Here both should still exist:

```text
Before restore point
After restore point
```

This is a useful safety property of this demo: you test restore without
destroying the production database.

## 13. Clean up the restore test

After inspecting the restore result and obtaining explicit approval to delete
only the disposable restore container/volume:

```bash
python3 /opt/todo/bin/app_backup.py --app todo cleanup-restore \
  --confirm todo-postgres-restore
```

This removes only the temporary restore container and restore volume.

It does not affect:

```text
todo-postgres-data
todo-postgres-backup
```

## What this exercise demonstrated

You have now tested:

```text
PostgreSQL primary
      |
      +-- base backup
      |
      +-- continuous WAL archive
                |
                +-- restore point
                       v
             isolated PITR restore
```

That means you do not just have a copy of the database — you can reconstruct
it to a specific point in time.

An important limitation is that `todo-postgres-backup` lives on the same VM.
It protects against things like accidental deletion or logical database
errors, but not against losing the whole VM or the Proxmox host. For real
backup, the content should also be copied to separate storage.
