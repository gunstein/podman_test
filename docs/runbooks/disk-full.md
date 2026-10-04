# The disk is filling up

**You notice it** when a timer fails with `only N% of the disk is free`
(below 10 %), or WAL archiving fails. A full disk stops PostgreSQL from
writing; act before that.

## 1. See what uses the space

```bash
df -h ~
podman system df
for db in todo notes keycloak; do
  echo "$db backup: $(podman unshare du -sh "$(podman volume inspect -f '{{.Mountpoint}}' $db-postgres-backup)" | cut -f1)"
  echo "$db pg_wal: $(podman exec $db-postgres du -sh /var/lib/postgresql/data/pg_wal | cut -f1)"
done
journalctl --disk-usage
```

## 2. Safe ways to free space

- **A lost slot holding WAL** (pg_wal far above its usual size, about 64 MB
  in the lab): see [standby-rebuild.md](standby-rebuild.md). Each slot keeps
  at most 1 GB per database.
- **A leftover PITR test**: `python3 /opt/todo/bin/app_backup.py --app todo
  restore-status`; if one exists, remove it with `cleanup-restore --confirm
  todo-postgres-restore` (likewise for notes).
- **Old images** from earlier bundles: `podman image prune` (only unused
  images; the running stack keeps its own).
- **The journal**: `sudo journalctl --vacuum-size=500M`.
- **Fewer days of backups**: the timer keeps 7. Each base backup is the size
  of its database (about 235 MB each in the lab). Shortening retention is a
  change to the timer's service, not a manual deletion.
- **More disk**: often the right answer, if the data has simply grown.

**Never** delete files in a backup volume, in `pg_wal` or in the WAL archive
by hand. A base backup is useless without the WAL after it, and PostgreSQL
cannot start without its own WAL. The nightly backup removes old backups and
the WAL only they needed in the right order.
