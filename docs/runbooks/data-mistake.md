# Data was deleted or changed by mistake

Replication copies the mistake to the standby within seconds, so the standby
does not help. The backups do, within limits.

## What is possible

- **To a time** (DR primary): `app_backup.py` restores the newest base backup
  from before that time and replays the archived WAL up to it. Name a time
  just before the mistake, with its UTC offset. Tested in every acceptance
  run (phase 8, Notes).
- **To a named restore point** (DR primary): the same, up to a point someone
  created with `app_backup.py mark` before a risky change. Tested in phase 8
  (Todo).
- **To last night** (single host, which has no WAL archive):
  [single-host-restore.md](single-host-restore.md). That replaces every
  database and loses everything written since.

The restore never touches the live database. It starts a separate, read-only
copy without network; you look at the data there and copy back what you need
by hand. The archive keeps the WAL of the last 7 days (the oldest kept base
backup), so the time must lie within them.

## Restore one database into the disposable copy

On the current primary (`--app` is todo, notes or keycloak; the time is when
the data was still right, here 14:36 Oslo summer time):

```bash
python3 /opt/platform/bin/app_backup.py status                     # archiving healthy?
python3 /opt/platform/bin/app_backup.py --app notes restore --target-time 2026-10-04T14:36:00+02:00
python3 /opt/platform/bin/app_backup.py --app notes restore-status  # t|t|on: paused at that time
podman exec notes-postgres-restore psql -U notes -d notes -c "SELECT ... ;"
```

It prints the base backup it chose. For a named point instead:
`--backup base-YYYYMMDDTHHMMSSZ --target before_cleanup` (the newest backup
taken before the point; list them with
`podman exec notes-postgres ls /var/lib/postgresql/backup/base`). If the
restore says it did not reach its target, the time is after the last change
in the archive or inside the chosen backup: pick another time or backup.

Read the rows you lost in `notes-postgres-restore`, and put them back into
the live database with ordinary SQL, as the app's own role would. Then
remove the copy:

```bash
python3 /opt/platform/bin/app_backup.py --app notes cleanup-restore --confirm notes-postgres-restore
```

Before a risky change, a named point still helps: it names the moment
exactly, `python3 /opt/platform/bin/app_backup.py mark --name before_cleanup`.

**Never** point a restore at the live volume, and never stop the live
database to restore it: the three databases are one group, and the standby
follows the live primary.
