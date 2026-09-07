# Application failover


Application recovery starts the application tier on an already promoted PostgreSQL standby. It
does not initialize PostgreSQL, change database roles or generate credentials. It
derives missing Kube-compatible secret objects only from the existing host-local
Podman secrets. The grouped application runs its normal idempotent schema migration as an init container.

The stable demo identity is:

```text
https://todo.test:8443
```

The same name must be used by nginx, Keycloak, the backend issuer check and the
Keycloak frontend client. During this two-VM LAN drill, the client maps the name
to the promoted host address. The production Helm render fixes this identity
at `todo.test:8443`; promotion changes only which host address serves it.

## Stage before an incident

Keep the operations package and the offline image bundle on standby before
an incident. The package contains no secrets, images, site-specific inventory or
database data.

Use the verified artifacts and inventory prepared in
[Acceptance](../docs/ACCEPTANCE.md#2-build-and-stage-artifacts). Stage both
packages on both hosts before an incident; verify checksums and matching clean
VERSION values before running extracted code.

## Application recovery contract

Follow [application failover](../docs/ACCEPTANCE.md#7-application-failover)
for recovery inventory, client-scoped HTTPS firewall, deployment, DNS and trust.
The playbook publishes HTTPS on the promoted host, with HTTP health access on
loopback. PostgreSQL and internal backend/Keycloak ports are not opened to clients.

The playbook fails before changing application state unless:

- it runs on the declared host and address;
- `todo-postgres.service` is active;
- PostgreSQL reports `f|off`, meaning promoted and writable;
- all four application runtime secrets exist;
- every missing application image has its corresponding staged offline archive.

It loads only missing images, installs the grouped `todo-app` and independent `todo-keycloak` Kube
workloads, starts them through `.kube` Quadlets, updates the existing Keycloak client to the stable
origin, and checks health, readiness, discovery and public Todo reads.

## Client name and certificate

Use the [client trust and browser checks](../docs/ACCEPTANCE.md#client-trust-and-real-browser-verification)
after failover. Replace the prior `todo.test` mapping and trust only the verified
public CA of the serving host; the private CA key stays inside `todo-nginx-data`.

### Certificate lifecycle and DR alternatives

There are two different artifacts and trust directions:

```text
promoted server: leaf certificate + private key
client:          public CA root used to verify that leaf certificate
```

When the promoted nginx container first starts, its entrypoint uses the image-packaged OpenSSL to create a local demo CA and a `todo.test` server certificate. The client can therefore receive the exact public root only
after application recovery. This is simple, works offline and never copies the
private demo CA key out of the TLS data volume. Its disadvantage is operational: manual
certificate distribution consumes failover time, requires a browser restart
on some clients and does not scale beyond a small lab.

The certificate path should normally be prepared before an incident. Common
alternatives are:

| Model | How it works | Advantages | Costs and risks |
|---|---|---|---|
| Pre-stage the local demo CA on standby | Before an incident, create the standby TLS volume with a controlled certificate-initialization run for `todo.test`; distribute its public root while the application remains unpublished | Keeps the offline internal-CA model and removes trust installation from the failover RTO | The inactive node already holds a CA private key; renewal, backup and permission/SELinux handling become pre-incident responsibilities |
| Organization internal PKI | Clients trust one organization root in advance; each node receives its own reviewed leaf certificate and private key for `todo.test` | Central trust, revocation and renewal; no client action during failover | Requires PKI and secure certificate/key provisioning; the root private key should not be copied to application nodes |
| Public CA / ACME | A publicly trusted CA issues `todo.test` or a real DNS name, commonly through automated ACME renewal | Browsers trust it without manual root installation; mature automation | Requires suitable DNS/domain validation and usually network dependencies; it is a poor fit for this intentionally offline lab |
| TLS-terminating load balancer | A stable, preferably redundant proxy owns the service certificate and routes to the active application node | Database/app failover does not change client TLS identity | Adds infrastructure, health routing and its own HA lifecycle |
| Copy one local demo CA to both nodes | Securely transfer the existing demo CA private material and let both nodes issue from the same root | Clients need trust only once | Expands the private CA key's exposure, couples the nodes and requires protected transfer, backup, file ownership and SELinux labels; avoid casual volume copying |

For this demo, the post-promotion copy is retained because it makes the trust
chain visible with minimal infrastructure. For a real DR design, prefer
pre-staged client trust and per-node leaf keys from an organization or public
CA. Certificate availability and renewal belong in the readiness checklist,
not in improvised work after the primary has failed.

## Verify locally on standby

```bash
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
curl --fail http://127.0.0.1:8080/api/todos

curl --fail \
  http://127.0.0.1:8080/auth/realms/todo/.well-known/openid-configuration
```

The discovery document issuer must be
`https://todo.test:8443/auth/realms/todo`.

The old primary must remain fenced. Rejoining or failing back is a separate
operation that starts by rebuilding it as a replica of the promoted database.

## Acceptance evidence

Use [Acceptance](../docs/ACCEPTANCE.md) for the full sequence and verdict.
[688a0f6](../docs/ACCEPTANCE-688a0f6.md) records historical evidence only.
