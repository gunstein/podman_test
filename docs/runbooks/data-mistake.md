# Data was deleted or changed by mistake

Replication copies the mistake to the standby within seconds, so the standby
does not help. The backups do, within limits.

## What is possible today

- **To a named restore point** (DR primary): `app_backup.py` restores a base
  backup and replays archived WAL up to a restore point someone **created
  before the mistake** with `app_backup.py mark`. Tested in every
  acceptance run (phase 8). There is no restore to a clock time yet, and the
  nightly backup creates no restore point: without a mark from before the
  mistake, the closest you can get is a base backup (backlog M5).
- **To last night** (single host): [single-host-restore.md](single-host-restore.md).
  That replaces every database and loses everything written since.

The restore never touches the live database. It starts a separate, read-only
copy without network; you look at the data there and copy back what you need
by hand. Take a mark before risky changes, so a way back exists:

```bash
python3 /opt/todo/bin/app_backup.py mark --name before_cleanup_2026_10_04
```

## Restore one database into the disposable copy

On the current primary (`--app` is todo, notes or keycloak):

```bash
python3 /opt/todo/bin/app_backup.py status                     # archiving healthy?
podman exec todo-postgres ls /var/lib/postgresql/backup/base   # base backups, by time
python3 /opt/todo/bin/app_backup.py --app todo restore \
  --backup base-YYYYMMDDTHHMMSSZ --target before_cleanup_2026_10_04
python3 /opt/todo/bin/app_backup.py --app todo restore-status  # t|t|on: paused at the point
podman exec todo-postgres-restore psql -U todo -d todo -c "SELECT ... ;"
```

Choose the newest base backup taken **before** the restore point. Read the
rows you lost in `todo-postgres-restore`, and put them back into the live
database with ordinary SQL, as the app's own role would. Then remove the
copy:

```bash
python3 /opt/todo/bin/app_backup.py --app todo cleanup-restore --confirm todo-postgres-restore
```

**Never** point a restore at the live volume, and never stop the live
database to restore it: the three databases are one group, and the standby
follows the live primary.
