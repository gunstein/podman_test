# Where the logs are

Everything the product runs on a host logs to journald: the seven pods'
containers, their user systemd units and the three timers. Nothing writes its
own log files. The commands that change things (`install.sh`, `app_installer`,
`app-ops`, `app_dr.py`) print to the terminal that runs them and keep no log;
acceptance keeps theirs in its run folder.

Commands run as the service user on the host unless they say `sudo`.

## What logs where

| What | Where it goes | Read it with |
|---|---|---|
| A container's output: nginx's access and error log, PostgreSQL, the backends, Keycloak, the migration init container | journald, through `LogDriver=journald` in every `.kube` unit; field `CONTAINER_NAME` | `podman logs nginx`, or `journalctl CONTAINER_NAME=nginx` |
| A pod's unit: start, stop, failure, restart | journald, the user unit (`todo-app.service`, ...) | `journalctl --user -u todo-app.service` |
| The timers' runs: nightly backup, DR check, replication certificate renewal | journald, their user services | `journalctl --user -u todo-backup.service -n 30 -o cat` |
| `install.sh`, `app_installer`, `app-ops`, `app_dr.py` | stdout (one JSON result) and stderr of the command | the terminal; keep it with `2>&1 \| tee` if you need it later |
| A promotion's decisions | the promotion record `~/.config/todo/promotion.json` on the promoted host, a file, not journald | `cat ~/.config/todo/promotion.json` |
| An acceptance run | `~/todo-acceptance-runs/<run ID>/logs/` on the client, one file per step | `REPORT.md` and `EVIDENCE.md` in the same folder |
| fapolicyd and SELinux denials | the system journal and the audit log | `sudo ausearch --start recent -m fanotify` and `-m avc` ([FAPOLICYD.md](../deploy/offline/FAPOLICYD.md)) |

Container names, by pod: `todo-postgres`, `notes-postgres`,
`keycloak-postgres`, `keycloak`, `todo-migrate` with `todo-backend` and
`todo-frontend` (pod `todo-app`), the same three for `notes-app`, and `nginx`
(pod `shared-proxy`). The unit is the pod's name plus `.service`.

## When `journalctl --user` shows nothing

`journalctl --user` reads the per-user journal files. journald only keeps
those when its storage is persistent (`/var/log/journal` exists); with
volatile storage, the user units' messages go into the system journal
instead, and `journalctl --user` finds nothing, even though the unit ran.
Read the system journal by field then:

```bash
journalctl -b _SYSTEMD_USER_UNIT=todo-postgres.service --no-pager | tail -n 20
journalctl -b CONTAINER_NAME=todo-postgres --no-pager | tail -n 20
```

That needs read access to the system journal: membership in `wheel`, `adm`
or `systemd-journal`, or `sudo`. Without it the command shows nothing too,
with a hint about the groups. `podman logs NAME` reads the same journal
(`LogDriver=journald`), so it has the same limits; on the other hand it still
finds the output of a container that was removed, as long as the journal
keeps it, by `journalctl CONTAINER_NAME=NAME`.

Whether logs survive a reboot, and how much is kept, depends on journald's
storage on the host, which the installer does not set (backlog L4). Check
with `journalctl --disk-usage` and `ls -d /var/log/journal`.

## Ready commands

The whole stack after a start or a reboot, newest last:

```bash
journalctl --user -b -u 'todo-*' -u 'notes-*' -u 'keycloak*' -u shared-proxy.service \
  -u app-network-network.service --no-pager | tail -n 50
```

One workload:

```bash
systemctl --user status shared-proxy.service      # state and the last lines
podman logs --since 15m nginx                       # nginx's access and error log
podman logs --since 15m todo-backend
podman logs todo-migrate                             # the migration run at the last start
podman logs --since 1h todo-postgres                 # PostgreSQL, replication included
podman logs --since 15m keycloak
```

The timers ([runbook](runbooks/timer-failed.md)):

```bash
systemctl --user list-timers 'todo-*'
journalctl --user -u todo-dr-check.service -n 30 -o cat
journalctl --user -u todo-backup.service -n 30 -o cat
journalctl --user -u todo-replication-tls.service -n 30 -o cat
```

Development (`dev-up.sh`, direct `podman kube play` without systemd): there
are no units, so read the containers with `podman logs NAME`.

## Not covered

The two hosts keep separate journals, so after a failover the history is
split between them, and a lost host's logs are lost with it (backlog L6).
Each command that changes things prints its result rather than logging it
(backlog L1).
