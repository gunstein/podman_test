# A single host must go back to last night

For a host installed with `install.sh` only, without DR. Every night at 02:30
`platform-backup.timer` takes a verified base backup of every database and keeps
7 days. There is no WAL archive on a single host: a restore goes back to the
**latest** backup, and everything written since is lost. Tested in every
acceptance run (phase 3).

## 1. Check that last night's backup is there

```bash
journalctl --user -u platform-backup.service -n 10 -o cat   # "verified base backup base-..."
podman exec todo-postgres cat /var/lib/postgresql/backup/LATEST
```

If the latest backup is older than you expect, the timer failed: see
[timer-failed.md](timer-failed.md) before you restore.

## 2. Restore, from the bundle the host was installed from

```bash
cd ~/platform-offline-m12
PYTHONPATH=deploy/installer python3 -m app_installer backup restore --confirm-restore "$(hostname)"
```

It checks every backup first and changes nothing if one is missing. Then it
stops the whole stack, replaces each data volume (Todo, Notes and Keycloak)
with its latest backup, and starts everything again. It prints the backup it
used for each database and `{"changed": true}`.

## 3. Check

Open both apps and log in. Users, passwords and data are as they were at the
time of the backup.

**Never** run this on a DR host: it refuses, because the DR tools own those
databases ([data-mistake.md](data-mistake.md) for DR). Copy the backup
volumes elsewhere if losing the machine must not lose the data: the backups
are on the same disk.
