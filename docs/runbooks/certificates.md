# A certificate expires or has expired

Four certificates, four different stories. See when each expires:

```bash
# nginx: the leaf users see, and the demo CA that signed it
podman exec nginx openssl x509 -in /var/lib/todo-tls/server.crt -noout -enddate
podman exec nginx openssl x509 -in /var/lib/todo-tls/ca.crt -noout -enddate
# replication: the primary's server certificates and the replication CA, in days
python3 /opt/todo/bin/app_dr.py check | grep -i 'certificate\|Replication CA'
```

## nginx leaf (397 days)

**You notice it** when browsers warn about an expired certificate. nginx
renews its leaf **when it starts** if less than 30 days are left, signed by
the same CA, so clients that trust the CA need nothing new:

```bash
systemctl --user restart shared-proxy.service
podman exec nginx openssl x509 -in /var/lib/todo-tls/server.crt -noout -enddate
```

A running nginx never renews by itself: a host that runs for over a year
without a restart lets it expire (backlog U2 adds a warning). Tested in
acceptance: every start of nginx checks and renews.

## nginx demo CA (10 years)

The same start renews the CA when less than 30 days are left. That is a
**new** CA: every client must trust it again, exactly as after a failover.
Plan it, do not let it happen by accident. Production should use its own CA
or a public one instead ([TLS.md](../TLS.md), backlog T4).

## Replication server certificate (825 days)

It is issued when a primary is published (`bootstrap-standby`,
`rebuild-standby`) and **renewed by itself**: `todo-replication-tls.timer`
runs `app_dr.py renew-tls` every night at 03:30 on both hosts. On the
primary it issues a new certificate for every database with fewer than 30
days left, for the same address, signed by the same replication CA, and
reloads PostgreSQL: no restart, and the standby needs nothing new. On the
standby it prints `standby, nothing to renew`.

**You notice a renewal that fails** in two places: the timer's own unit
fails (`journalctl --user -u todo-replication-tls.service`), and once a
certificate has fewer than 25 days left the DR check fails with
`replication certificate expires in N days; todo-replication-tls.timer has
not renewed it`. Read the timer's `ERROR:` line, fix the cause and renew
at once on the primary:

```bash
journalctl --user -u todo-replication-tls.service -n 20 -o cat
python3 /opt/todo/bin/app_dr.py renew-tls
python3 /opt/todo/bin/app_dr.py check
```

| Message | Meaning | Do |
|---|---|---|
| `the primary has no replication certificate` | The primary was never published | `bootstrap-standby` or `rebuild-standby` from the operations package |
| `the replication certificate names no address` | Not a certificate this tool issued | Publish the primary again, as above |
| An `openssl` or `podman secret` command failed | The replication CA secret is missing or unreadable | `sync-standby-secrets` (the DR check also names missing DR secrets) |

Renewal keeps the address the certificate names. A primary whose LAN
address changed must be published again (`rebuild-standby`), not renewed.

If the certificate expired anyway, the standby stops receiving WAL with a
TLS error in its PostgreSQL log (`journalctl --user -u todo-postgres.service`)
and the DR check fails with `the standby does not receive WAL`. Run
`renew-tls` on the primary as above: the standby connects again by itself
with the new certificate.

## Replication CA (10 years)

Both hosts hold it, and nothing renews it. The DR check fails on both hosts
once it has fewer than 180 days left (`Replication CA expires in N days`).
Replacing it means a new CA on both hosts and a new certificate on the
primary: **not tested, no command** (backlog U2). Plan it from the first
warning.

**Never** turn TLS off or lower `sslmode` to get replication running again:
the stream carries every row across the network between the sites.
