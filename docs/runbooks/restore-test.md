# Prove the backups, once a month

A backup that was never restored is not proven. Acceptance restores in the
lab on every run; production needs its own proof, on its own data. Once a
month, on the current primary, restore each database into the disposable
copy and check one row you know. It touches nothing live, takes a few
minutes per database, and is the same command as a real
[data mistake](data-mistake.md).

## On a DR primary

Pick a time a few hours ago, with its UTC offset, and for each database:

```bash
python3 /opt/todo/bin/app_backup.py status                     # archiving healthy, no failed WAL
for app in todo notes keycloak; do
  python3 /opt/todo/bin/app_backup.py --app "$app" restore --target-time 2026-11-02T09:00:00+01:00
  python3 /opt/todo/bin/app_backup.py --app "$app" restore-status   # t|t|on: paused at that time
  # Look at one row you know existed then, for example the newest before that time:
  #   podman exec todo-postgres-restore psql -U todo -d todo -c "SELECT max(id) FROM todos;"
  #   podman exec notes-postgres-restore psql -U notes -d notes -c "SELECT max(id) FROM notes;"
  #   podman exec keycloak-postgres-restore psql -U keycloak -d keycloak -c "SELECT count(*) FROM user_entity;"
  python3 /opt/todo/bin/app_backup.py --app "$app" cleanup-restore --confirm "$app-postgres-restore"
done
```

Pass when every restore reaches its time, `restore-status` says `t|t|on`,
and each query answers with what you expected. Write down the date, the
backups it chose and the answers; that record is the proof.

## On a single host

A single host has no WAL archive, and its restore replaces the live data
([single-host-restore.md](single-host-restore.md)), so do not run it on the
production host to test it. Instead, check the nightly run proved the
backup it took (it verifies each one), and once a quarter restore on a
spare host or VM installed from the same bundle with a copy of the backup
volumes:

```bash
journalctl --user -u todo-backup.service -n 10 -o cat   # "verified base backup base-..."
```

## When it fails

A restore that does not reach its time, a missing WAL file or a query that
answers wrong means the backups would not save you today. Do not wait for
the next month: see [timer-failed.md](timer-failed.md) for the nightly
backup and the archive, and take a new base backup
(`python3 /opt/todo/bin/app_backup.py create`) once archiving is healthy.
