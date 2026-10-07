# A timer failed: todo-dr-check, todo-backup or todo-replication-tls

Every timer turns a problem into a **failed unit**; nobody is paged. Look
daily, on both DR hosts (and on a single host for the backup):

```bash
systemctl --user --failed
journalctl --user -u todo-dr-check.service -n 30 -o cat
journalctl --user -u todo-backup.service -n 30 -o cat
journalctl --user -u todo-replication-tls.service -n 30 -o cat
```

Each problem is one `ERROR:` line. The check runs every 15 minutes; it
clears itself on the next run after the cause is gone, or start it now with
`systemctl --user start todo-dr-check.service`.

## todo-dr-check (DR hosts, every 15 minutes)

| Message | Meaning | Do |
|---|---|---|
| `no standby streams from this primary over TLS` | The primary has no standby. Expected after a failover until the rebuild | [standby-rebuild.md](standby-rebuild.md) |
| `replication slot ... is inactive, losing WAL or invalidated` | The standby is away, or too long away | [standby-rebuild.md](standby-rebuild.md) |
| `the standby does not receive WAL from the primary` | Seen on the standby: no connection to the primary | Is the primary up? Firewall 5432-5434? If the primary is lost: [primary-lost.md](primary-lost.md) |
| `WAL archiving has not recovered from its most recent failure` | The archive copy fails (often a full disk) | [disk-full.md](disk-full.md), then `python3 /opt/todo/bin/app_backup.py status` |
| `the group is split: ...` | Some databases are primary, some standby. Serious | Stop. Do not promote or rebuild anything; inspect `python3 /opt/todo/bin/app_dr.py status` and `~/.config/todo/promotion.json` |
| `only N% of the disk is free` | Less than 10 % free | [disk-full.md](disk-full.md) |
| `the offline bundle at ... is revision ...` | This host's bundle differs from the operations package that installed the DR tool | Stage the same revision on both hosts, then `install-dr-tool` again |
| `no complete offline bundle at ...`, `lacks image archives` | A failover here could not load its images | Copy and verify the bundle again (`sha256sum -c`) |
| `DR secrets missing on this host: ...` | A failover here could not start | `sync-standby-secrets` from the operations package with `initial.yaml` |
| `Cannot read valid DR configuration` | The DR tool is not set up on this host | `install-dr-tool` |
| `replication certificate expires in N days; todo-replication-tls.timer has not renewed it` | The nightly renewal has failed for several nights | [certificates.md](certificates.md#replication-server-certificate-825-days) |
| `replication certificate: cannot read its expiry` | The primary has no certificate, or openssl could not read it | [certificates.md](certificates.md#replication-server-certificate-825-days) |
| `Replication CA expires in N days` | The replication CA must be replaced by hand | [certificates.md](certificates.md#replication-ca-10-years) |

## todo-backup (every night at 02:30)

| Message | Meaning | Do |
|---|---|---|
| `only N% of the disk is free` | The backup was taken, but the disk is low | [disk-full.md](disk-full.md) |
| `the nginx certificate expires in N days` | The backup was taken; nginx's certificate from your CA needs renewing | [certificates.md](certificates.md#nginx-certificate-from-your-own-ca-provided-mode) |
| `the nginx demo certificate expires in N days` | The backup was taken; nginx renews its demo certificate only when it starts | [certificates.md](certificates.md#nginx-leaf-397-days-local-mode) |
| `cannot check the nginx certificate: ...` | The backup was taken; looking at the certificate failed | `PYTHONPATH=deploy/installer python3 -m app_installer tls-status` shows why |
| `archive_mode is not on` | A DR primary without WAL archiving | `configure-backup` from the operations package with `recovery.yaml` |
| `Live PostgreSQL is not a writable promoted primary` | A DR backup on a host whose databases are not all writable primaries | `python3 /opt/todo/bin/app_dr.py status`; on a split group, as below |
| `the latest verified backup ... is missing; nothing was deleted` | The newest backup the marker names is gone | Do not delete anything; take a new one: `systemctl --user start todo-backup.service` |
| `Some databases are standbys and some are not` | Split group | As for the split group above |
| A command failed (`pg_basebackup`, `pg_verifybackup`) | The copy or its check failed | Read the lines before it; check disk and database health; start the service again |

On a standby, `standby: nothing to back up` is normal: the primary takes the
backups.

## todo-replication-tls (DR hosts, every night at 03:30)

Each database's line says whether its certificate was kept or renewed, and
for how many days it is valid. On a standby `standby, nothing to renew` is
normal. Any `ERROR:` line: [certificates.md](certificates.md#replication-server-certificate-825-days).
