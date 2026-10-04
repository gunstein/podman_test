# A certificate expires or has expired

Three certificates, three different stories. See when each expires:

```bash
# nginx: the leaf users see, and the demo CA that signed it
podman exec nginx openssl x509 -in /var/lib/todo-tls/server.crt -noout -enddate
podman exec nginx openssl x509 -in /var/lib/todo-tls/ca.crt -noout -enddate
# replication: the primary's server certificate, per database
podman exec todo-postgres cat /var/lib/postgresql/data/server.crt | openssl x509 -noout -enddate
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

**You notice it** when the standby stops receiving WAL with a TLS error in
the standby's PostgreSQL log (`journalctl --user -u todo-postgres.service`),
and the DR check fails with `the standby does not receive WAL`.

It is issued when a primary is published: at `bootstrap-standby` and at
`rebuild-standby`, which renew it if less than 30 days are left. A pair that
runs that long without either needs a renewal step that does not exist yet:
**not tested, no command** (backlog U2). Note the expiry date at bootstrap
and plan before it: until U2, re-seeding the standby within the last 30
days ([standby-rebuild.md](standby-rebuild.md), case 2b) issues a new one.

**Never** turn TLS off or lower `sslmode` to get replication running again:
the stream carries every row across the network between the sites.
