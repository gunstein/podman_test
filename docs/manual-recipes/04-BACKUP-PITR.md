# Backup and PITR on one VM

This builds on [Offline install on one VM](02-OFFLINE-INSTALL.md). You should
already have a working Todo installation on one VM.

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

## 1. Build the operations package on the laptop

From the repository:

```bash
git switch feature/podman-kube

scripts/build-operations-package.sh

cd dist
sha256sum -c todo-operations.tar.gz.sha256
```

Copy the package to the VM:

```bash
scp todo-operations.tar.gz \
    todo-operations.tar.gz.sha256 \
    todo@192.168.1.50:
```

## 2. Extract on the VM

On the VM:

```bash
cd ~

sha256sum -c todo-operations.tar.gz.sha256

mkdir -p todo-operations

tar -xzf todo-operations.tar.gz \
  --strip-components=1 \
  --directory todo-operations

cd todo-operations
```

## 3. Build a simple inventory

Copy the recovery example:

```bash
cp ansible/inventory-recovery.example.ini \
   ansible/inventory-recovery.ini
```

For a single-VM lab, we can use the VM as `todo_current_primary`.

Example with:

```text
VM = 192.168.1.50
user = todo
```

Substitute the values:

```bash
sed -i \
  -e 's/192\.0\.2\.11/192.168.1.50/g' \
  -e 's/ansible_user=gunstein/ansible_user=todo/' \
  -e 's#/home/gunstein#/home/todo#g' \
  ansible/inventory-recovery.ini
```

## 4. Install and configure the backup feature

Run:

```bash
ansible-playbook \
  --ask-become-pass \
  --inventory ansible/inventory-recovery.ini \
  ansible/configure-backup.yml
```

This installs:

```text
/opt/todo/bin/todo_backup.py
```

and creates a separate Podman volume:

```text
todo-postgres-backup
├── base/
└── wal/
```

The playbook enables PostgreSQL WAL archiving and checks that WAL actually
lands in the backup volume.

## 5. Check backup status

```bash
python3 /opt/todo/bin/todo_backup.py status
```

Check the volume too:

```bash
podman volume ls | grep todo-postgres
```

You should see both:

```text
todo-postgres-data
todo-postgres-backup
```

## 6. Take a base backup

```bash
python3 /opt/todo/bin/todo_backup.py create
```

The tool uses `pg_basebackup`, creates a SHA-256 manifest and runs
`pg_verifybackup`.

You get back a backup name, for example:

```text
base-20260907T081500Z
```

Keep the name. You need it for the restore.

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
python3 /opt/todo/bin/todo_backup.py mark \
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
python3 /opt/todo/bin/todo_backup.py restore \
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
python3 /opt/todo/bin/todo_backup.py restore-status
```

Then:

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

When done:

```bash
python3 /opt/todo/bin/todo_backup.py cleanup-restore \
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
