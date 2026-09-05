# System architecture

This is the architectural overview of the current Todo demo: what runs,
which boundary owns each responsibility, and why. It describes the
implementation, not an aspirational production platform.

Use the [Learning Guide](LEARNING-GUIDE.md) to explore it, the
[acceptance checklist](MANUAL-DR-QUICKSTART.md) to prove it, and operational
runbooks to change it. This document intentionally contains no destructive
command recipes or chronological test logs.

## 1. Goals and non-goals

The demo evaluates Podman Kube YAML as a shared development and deployment
workload format against the historical per-container Quadlet reference.
It demonstrates plain browser JavaScript, FastAPI, PostgreSQL, nginx,
identity, rootless execution, offline delivery and recovery.

The supported Kubernetes YAML subset is a **Podman workload format**.
There is no Kubernetes cluster, scheduler, cross-runtime portability promise,
Docker Compose, Node.js frontend framework or resident Ansible agent.
The system does not implement automatic HA, seamless failover, multi-tenant
authorization, managed PKI or an off-host backup service.

The accepted historical implementation remains recoverable through
`quadlet-reference-v1`. Its transition tools are source-only evidence,
not part of normal deployment or the operations package.

## 2. System context and requests

```text
Browser (plain HTML/CSS/JS)
    │ HTTPS: stable service name todo.test
    ▼
nginx ── /api/, /health, /ready ──► FastAPI ──► PostgreSQL
    │                                            ▲
    └── /auth/ ─────────────────► Keycloak ───────┘
```

nginx serves the frontend assets and terminates TLS. Public Todo reads are
allowed; create, update and delete require a valid access token.
Todos are shared: authentication does not imply per-user row ownership.
Both FastAPI and Keycloak persist data in PostgreSQL, using different
identities and privilege boundaries. Keycloak uses its own schema.

Clients reach the stable HTTPS hostname rather than a pod address.
In the lab the external port is 8443. Port 8080 is published on loopback for
local checks; remote HTTPS and replication publication are explicit
deployment choices constrained by host firewalls.

## 3. Runtime topology: group by lifecycle

```text
One service user's rootless Podman network: todo-network
  ├── todo-app pod
  │     ├── todo-migrate (init: schema migration)
  │     ├── todo-backend (FastAPI)
  │     └── todo-frontend (nginx and static assets)
  ├── todo-keycloak pod
  │     └── todo-keycloak
  └── todo-postgres pod
        └── todo-postgres
```

The app's migration must succeed before its regular containers start.
Backend and proxy share app deployment and restart semantics. PostgreSQL
and Keycloak have independent lifecycles so app changes do not implicitly
replace database or identity state. A rebuilt standby runs only PostgreSQL.

The canonical definitions are under `kube/runtime/`, rendered from
`helm/todo/`. Each pod has one `.kube` unit and generated user service:
`todo-app.service`, `todo-keycloak.service`, `todo-postgres.service`.
The units use `--no-pod-prefix` to preserve operational container names;
the tested target platform requires Podman 5.8.2.

## 4. Responsibility boundaries

| Layer | Owns | Does not own |
|---|---|---|
| Helm | Build-time templates and non-secret environment values | Target-host runtime or orchestration |
| Kube YAML | Pod contents, init ordering, runtime settings and secret references | Infrastructure fencing or host policy |
| .kube Quadlet | Binding a workload to user systemd, published ports and dependencies | Database failover decisions |
| systemd | Service ordering, restart, shutdown and boot behavior | PostgreSQL replication correctness |
| Ansible | Provisioning, deployment, security integration and assertions | Continuous runtime supervision |
| Python tools | Guarded DR, backup and resumable operator stages | A second configuration-management system |
| Podman | Rootless pods, containers, networks, volumes and secrets | Cluster scheduling |

Host integration also uses shared `.network` and `.volume` Quadlets.
User lingering enables services to run before interactive login.
`todo-app.service` depends on PostgreSQL and Keycloak; PostgreSQL also has
its own boot entrypoint to support a database-only host.

Ordering is not readiness. Init-container success, health checks, systemd
restart and Ansible readiness assertions address different conditions.
The PostgreSQL unit applies health-on-failure kill so unhealthy database
containers are replaced through the systemd lifecycle.

## 5. Build and deployment pipeline

```text
Helm chart + values ──► rendered YAML (checked-in; drift checked in CI)
                                  │
Containerfiles ──► OCI images ─────┤
                                  ▼
                 offline and operations artifacts
                 VERSION + archive checksums
                                  │
                                  ▼
                 Ansible on target ──► .kube ──► systemd ──► Podman
```

Helm runs on the build host, not the Oracle Linux target. Images and rendered
definitions are delivered offline; target execution does not fetch from a
registry. Both acceptance artifacts must identify the same clean revision.
Checksums establish integrity against the supplied digest, not publisher
identity; organizational artifact signing is not implemented.

Development uses the same chart with development values and direct
`podman kube play/down`. Production uses user systemd. These are different
lifecycle owners; development cleanup must not target a production user store.

Clean deployment performs privileged database-role provisioning separately
from ordinary schema migration. The app init container runs the idempotent
migrator using its dedicated credential. Promoted application recovery does
not rerun administrative role bootstrap.

## 6. Network and data flows

| Flow | Address boundary | Purpose |
|---|---|---|
| Browser → nginx | Published host HTTPS endpoint | Assets, API and identity proxy |
| nginx → backend | 127.0.0.1:8000 inside app pod | API, health and readiness |
| nginx → Keycloak | todo-keycloak:8080 on rootless network | OIDC browser endpoints under /auth |
| Backend → PostgreSQL | todo-postgres:5432 | Application queries with restricted DB role |
| Migrator → PostgreSQL | todo-postgres:5432 | Schema changes with migration identity |
| Keycloak → PostgreSQL | todo-postgres:5432 | Identity persistence with Keycloak identity |
| Backend → Keycloak | Internal configured JWKS endpoint | Signing-key retrieval for JWT validation |
| Standby → current primary | Explicit host TCP5432 publication | Physical replication |

The network resource is declared in `todo.network`; its runtime name is
`todo-network`. Loopback is shared only within a pod. Cross-pod communication
uses Podman DNS, not host IPs. The production workload supplies the grouped
nginx configuration with a loopback backend upstream.

Internal service HTTP and trusted-LAN replication are not universally
TLS-enforced. The external HTTPS boundary must not be mistaken for encryption
of every internal flow.

## 7. Identity architecture

```text
app.js → auth.js → keycloak-adapter.js → Keycloak SDK / OIDC endpoints
```

Todo UI uses init, isAuthenticated, login, logout, getAccessToken and
getUsername. The adapter owns SDK configuration, check-sso, S256 PKCE,
redirects and token refresh. Token-refresh errors propagate rather than
returning a stale token. Tokens are not deliberately persisted by Todo code.

Backend validation is independent of the frontend adapter: it validates JWT
signature, issuer and audience using configured JWKS. The public issuer remains
`https://todo.test:8443/auth/realms/todo`; JWKS can be fetched through the
internal Keycloak address without changing that issuer.

Only Keycloak is implemented. Another provider requires a new adapter, provider
configuration, compatible token claims and real browser/integration testing.
An adapter seam is not evidence that Duende or another provider already works.

## 8. Persistence and secrets

| State | Storage / identity | Lifecycle |
|---|---|---|
| App and identity database data | todo-postgres-data | Survives app replacement; explicitly replaced only during approved reseed |
| nginx CA and leaf-key state | todo-nginx-data | Survives local app recreation; promotion may create a new demo CA |
| Base backups and WAL | todo-postgres-backup | Separate from live data; still on the same VM |
| Runtime credentials | Host-local Podman secrets | Provisioned and transferred separately from YAML |

The bootstrap/admin, migrator, application, Keycloak and replication
identities have different jobs. Ansible constructs Kube-compatible secret
objects from existing secrets in memory. Secret values do not belong in Helm
values, rendered YAML, Git or transcripts. Sensitive transfer tasks use SSH
and suppress value-bearing output with no_log.

Filesystem ownership, rootless UID mapping and SELinux labels are independent.
A named volume is persistent storage, not a backup policy. Recovery assumes
a surviving database node and required credentials.

## 9. Security boundaries

- **Rootless Podman:** containers run under a service user with subordinate
  UID/GID mappings, not a root-owned container daemon.
- **SELinux:** enforcing labels constrain access; shared versus private volume
  labels differ from ownership adjustments.
- **fapolicyd:** Ansible maintains exact file trust and waits boundedly for the
  active database to match canonical path, size and SHA-256.
- **firewalld:** guest rules restrict client HTTPS and peer replication.
  Proxmox quarantine is a separate host-external boundary.
- **TLS:** nginx terminates HTTPS using OpenSSL-generated demo CA material;
  public CA trust must be updated explicitly when identity material changes.
- **Guest Agent:** execution and the specific helper's unconfined SELinux
  entrypoint require explicit opt-in. The transition is privileged; the small
  validated helper and reviewed hypervisor access are part of the trust boundary.

Quarantine installation restores existing persistent file labels after atomic
replacement. It does not silently grant new Guest Agent policy permissions.
Do not disable SELinux or fapolicyd to repair application failures.

## 10. Availability and disaster recovery

Initially one host serves the full application and a second streams PostgreSQL
WAL asynchronously. Physical slots retain needed WAL within a configured bound;
lag and invalidated slots require monitoring. Async replication cannot guarantee
that unsent commits survive abrupt loss.

```text
verify infrastructure fencing
  → standby preflight and explicit promotion
  → recover application and client routing/trust
  → verify backup/PITR
  → boot old primary with network disconnected
  → stop old services through reviewed Guest Agent helper
  → reconnect under tested quarantine
  → verify authenticated replication access
  → explicitly replace old database with new base backup
  → verify read-only standby, replication and sequential reboots
```

An unreachable TCP endpoint is not sufficient fencing evidence. The old primary
must never return unrestricted after promotion. The stop helper does not fence
the VM or authorize promotion/reseed: it checks identity, unit states, zero
service PIDs and no running user containers. A failed-but-stopped unit is
reported without erasing failure evidence.

Quarantine permits reviewed SSH and, only for guarded rebuild, outbound
replication to the current primary. It remains in place through verification.
After rebuild, the original stop helper is not a standby lifecycle tool:
the rebuilt host no longer has the original application units.

Promotion restores availability; rebuild restores redundancy. Returning the
application to the originally named primary is a separate switchover, not an
automatic DR step. Machine names stay fixed while roles change.

Base backup plus archived WAL supports a named-point restore into a fixed,
disposable, network-disabled test database. Live data is never a PITR test
target. Verified test cleanup removes only disposable resources. Backup
retention, off-host copying, encryption and alerts remain production work.

## 11. Verification status and production limits

Repaired lab runs have demonstrated promotion, application recovery,
backup/PITR, rebuild, persistent markers and sequential reboots.
The complete unchanged-revision Oracle Linux acceptance is still pending;
the new adapter needs real Keycloak verification in that run.
See [runtime results](../kube/runtime/RESULTS.md). Static tests or a green CI
run do not replace the full two-VM test.

This is a production-shaped educational demo, not a complete production
platform: one standby, shared Todos, manual client routing and CA trust,
operator fencing, on-VM backup, no automatic HA, no full observability stack
and no validated alternate IdP. Simultaneous loss of both database nodes is
outside the demonstrated recovery scope.

## 12. Detailed documentation

| Question | Document |
|---|---|
| How do I learn it? | [Learning Guide](LEARNING-GUIDE.md) |
| What is demonstrated versus simplified? | [Concept coverage](WHAT-YOU-LEARN.md) |
| Which definitions implement the pods? | [Kube runtime](../kube/runtime/README.md) |
| How is replication arranged? | [Standby architecture](../ansible/STANDBY-ARCHITECTURE.md) |
| How do I run acceptance safely? | [Operator checklist](MANUAL-DR-QUICKSTART.md), [acceptance criteria](LAB-ACCEPTANCE.md) |
| How does old-primary isolation work? | [Quarantine](PROXMOX-QUARANTINE.md) |
| How do backup and reseeding work? | [Backup/PITR](../ansible/BACKUP-PITR.md), [restore redundancy](../ansible/RESTORE-REDUNDANCY.md) |
| How are security details handled? | [SELinux](SELINUX.md), [secrets](SECRETS.md), [TLS](TLS.md) |

Source paths identify implementation, not a second source of configuration.
When code and this overview diverge, verify the implementation and correct the
document; never use prose alone as authorization for a destructive operation.
