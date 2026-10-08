# A certificate expires or has expired

Four certificates, four different stories. See when each expires:

```bash
# nginx: its TLS mode (local or provided) and how many days are left
PYTHONPATH=deploy/installer python3 -m app_installer tls-status
# nginx: the leaf users see, and the CA that signed it
podman exec nginx openssl x509 -in /var/lib/todo-tls/server.crt -noout -enddate
podman exec nginx openssl x509 -in /var/lib/todo-tls/ca.crt -noout -enddate
# replication: the primary's server certificates and the replication CA, in days
python3 /opt/todo/bin/app_dr.py check | grep -i 'certificate\|Replication CA'
```

## nginx certificate from your own CA (provided mode)

Set up as in [TLS.md](../TLS.md#provided-mode-the-organisations-own-ca). The
nightly backup run checks it: 60 days before the end it prepares
`~/.config/todo/nginx-tls-request.csr` (a new key waits in the TLS volume),
and below 30 days it fails with `the nginx certificate expires in N days`.
Take the request to the CA machine, sign it, and install the result:

```bash
python3 deploy/scripts/app_ca.py sign --directory /media/ca-usb/todo-ca \
  --request nginx-tls-request.csr --output host.crt          # on the CA machine
PYTHONPATH=deploy/installer python3 -m app_installer tls-install \
  --certificate ~/host.crt --ca ~/ca.crt                      # on the host
```

| Message | Meaning | Do |
|---|---|---|
| `tls-install`: `belongs to neither the waiting request ... nor the installed key` | It was signed from another host's request, or from an older one replaced with `--new-key` | Sign the request this host made last |
| `tls-install`: `not valid for NAME from this CA` | It misses a hostname nginx serves, is expired, or came from another CA | Check `--ca`; make a new request (`tls-request`) and sign it |
| nginx does not start: `ERROR: provided TLS mode, but ...` in `journalctl --user -u shared-proxy.service` | A file in the TLS volume is missing or does not fit, often after a new hostname was added | `tls-request`, sign, `tls-install`; nginx never falls back to the demo CA |
| `does not know provided TLS mode` | The proxy image predates provided mode | Rebuild it (`install --refresh-images`) or load it from a current offline bundle |

## nginx leaf (397 days, local mode)

**You notice it** when browsers warn about an expired certificate. nginx
renews its leaf **when it starts** if less than 30 days are left, signed by
the same CA, so clients that trust the CA need nothing new:

```bash
systemctl --user restart shared-proxy.service
podman exec nginx openssl x509 -in /var/lib/todo-tls/server.crt -noout -enddate
```

A running nginx never renews by itself: a host that runs for over a year
without a restart would let it expire. On a single host the nightly backup run
fails below 30 days with `the nginx demo certificate expires in N days`; the
restart above renews it. Tested in acceptance: every start of nginx checks
and renews.

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
