# Where the logs are

Everything the product runs on a host logs to journald: the seven pods'
containers, their user systemd units and the three timers. The commands that
change things (`app_installer`, so also `install.sh`, `app_dr_host`,
`app-ops`, `app_dr.py`, `app_backup.py`) print their output to the terminal
and leave one journald line per run: the command, the database it chose, its
exit code and how long it took, never an argument's value
(`app_installer/oplog.py`). app-ops also keeps everything it printed, one file
per run, on the controller. Acceptance keeps its own logs in its run folder.

Commands run as the service user on the host unless they say `sudo`.

## What logs where

| What | Where it goes | Read it with |
|---|---|---|
| A container's output: nginx's access and error log, PostgreSQL, the backends, Keycloak, the migration init container | journald, through `LogDriver=journald` in every `.kube` unit; field `CONTAINER_NAME` | `podman logs nginx`, or `journalctl CONTAINER_NAME=nginx` |
| A pod's unit: start, stop, failure, restart | journald, the user unit (`todo-app.service`, ...) | `journalctl --user -u todo-app.service` |
| The timers' runs: nightly backup, DR check, replication certificate renewal | journald, their user services | `journalctl --user -u platform-backup.service -n 30 -o cat` |
| `app_installer`, `app_dr_host`, `app-ops`, `app_dr.py`, `app_backup.py`: that they ran | one journald line per run, tagged with the tool | `journalctl -t app-installer -t app-dr-host -t app-ops -t app-dr -t app-backup` |
| The same: what they printed | stdout (one JSON result) and stderr of the command | the terminal; app-ops also in `~/.local/state/platform/app-ops/<time>-<command>.log` on the controller |
| A promotion's decisions | the promotion record `~/.config/platform/promotion.json` on the promoted host, a file, not journald | `cat ~/.config/platform/promotion.json` |
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

## Keep the journal across reboots, bounded

Whether logs survive a reboot, and how much is kept, is journald's host
setting, which the rootless installer cannot change. Set it once per host,
as root:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '%s\n' '[Journal]' 'Storage=persistent' 'SystemMaxUse=1G' 'MaxRetentionSec=3month' |
  sudo tee /etc/systemd/journald.conf.d/50-platform.conf
sudo systemctl restart systemd-journald
journalctl --disk-usage; ls -d /var/log/journal
```

`Storage=persistent` keeps the journal in `/var/log/journal`, so a reboot
keeps it and `journalctl --user` has the user's own files; `SystemMaxUse`
and `MaxRetentionSec` bound its size and age, whichever comes first. Choose
the numbers for your disk and how far back an incident needs to look. The
acceptance readiness check warns on a host without `/var/log/journal`, and
the lab's `prepare-agent-snapshots.sh` sets exactly this file before it
takes the clean snapshots.

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
journalctl --user -u platform-dr-check.service -n 30 -o cat
journalctl --user -u platform-backup.service -n 30 -o cat
journalctl --user -u platform-replication-tls.service -n 30 -o cat
```

Development (`dev-up.sh`, direct `podman kube play` without systemd): there
are no units, so read the containers with `podman logs NAME`.

## Not covered

The two hosts keep separate journals, so after a failover the history is
split between them, and a lost host's logs are lost with it (backlog L6).
On the hosts, the commands' own output stays in the terminal of whoever ran
them (app-ops over SSH keeps it on the controller); only their one journald
line stays on the host.
