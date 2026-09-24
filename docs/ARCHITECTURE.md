# System architecture

This is the architectural overview of the current Todo and Notes demo: what runs,
which boundary owns each responsibility, and why. It describes the
implementation, not an aspirational production platform.

Use the [Learning Guide](LEARNING-GUIDE.md) to explore it, the
[acceptance checklist](ACCEPTANCE.md) to prove it, and operational
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
`quadlet-reference-v1`. Retired transition tools remain in pre-retirement
Git history (`c377161`), not in the active tree or operations package.

## 2. System context and requests

```text
Browser (plain HTML/CSS/JS)
    │ HTTPS: profile-specific service identity
    ▼
shared nginx proxy ── /api/, /health, /ready ──► FastAPI ──► PostgreSQL
    │                                            ▲
    └── /auth/ ─────────────────► Keycloak ───────┘
```

The shared nginx proxy selects the application by hostname: `todo.test` or
`notes.test`. Each host routes `/` to its HTTP-only frontend and `/api/`,
`/health`, `/ready` to its own FastAPI backend. Both expose shared Keycloak at
`/auth/`. Reads are public; writes require a valid app-specific access token.
Rows are shared rather than owned per user.
Each app has independent PostgreSQL data, bootstrap, migration and runtime
identities. Keycloak has its own PostgreSQL pod, `keycloak-postgres`; shared
login therefore depends on that database, not on Todo's.

Clients reach the profile's HTTPS hostname rather than a pod address.
Both local and production profiles use `https://todo.test:8443` and
`https://notes.test:8443`. One SAN certificate covers both hostnames. Map both
names to the serving host; direct development uses 127.0.0.1. The shared values
file sets the canonical identity hostname/port, while the App registry supplies
additional application hostnames.
Port 8080 is published on loopback for
local checks; remote HTTPS and replication publication are explicit
deployment choices constrained by host firewalls.

## 3. Runtime topology: group by lifecycle

```text
One service user's rootless Podman network: app-network
  ├── shared-proxy pod
  │     └── nginx (TLS and routing)
  ├── todo-app pod
  │     ├── todo-migrate (init: schema migration)
  │     ├── todo-backend (FastAPI)
  │     └── todo-frontend (HTTP static assets)
  ├── notes-app pod
  │     ├── notes-migrate (init: schema migration)
  │     ├── notes-backend (FastAPI)
  │     └── notes-frontend (HTTP static assets)
  ├── notes-postgres pod
  │     └── notes-postgres
  ├── keycloak pod
  │     └── keycloak
  ├── keycloak-postgres pod
  │     └── keycloak-postgres
  └── todo-postgres pod
        └── todo-postgres
```

The app's migration must succeed before its regular containers start.
Backend and frontend share app deployment and restart semantics. The shared
proxy is a separate ingress boundary that can serve additional services. This
demo uses explicit Podman DNS routes with nginx DNS re-resolution; it needs no dynamic proxy platform, service discovery
framework or additional orchestration. PostgreSQL
and Keycloak have independent lifecycles so app changes do not implicitly
replace database or identity state. A rebuilt standby runs only PostgreSQL.

The canonical definitions are under `generated/kube-runtime/`, rendered from
the Jinja2 templates in `deploy/manifests/*.yaml.j2`. Each of the seven pods has
one `.kube` unit and generated user service:
`shared-proxy.service`, `todo-app.service`, `notes-app.service`,
`keycloak.service`, `todo-postgres.service`, `notes-postgres.service`,
`keycloak-postgres.service`. `apps.services()` returns this list. The proxy uses the operational container name `nginx`
and the persistent TLS volume `todo-nginx-data`. It reaches the frontend/backend
at `<app>-app:8080`/`<app>-app:8000` and Keycloak at `keycloak:8080`.
Loopback is shared only within a pod; it cannot connect the separate proxy to Todo.
The units use `--no-pod-prefix` to preserve operational container names;
the pinned OL9 lab baseline is Podman 5.8.2. The shared Python installer additionally verifies
that `podman kube play` exposes the required `--no-pod-prefix` capability.
This is a tested baseline, not a claim about the capability's minimum version.

## 4. Responsibility boundaries

| Layer | Owns | Does not own |
|---|---|---|
| Jinja2 manifest templates | Build-time templates and non-secret environment values | Target-host runtime or orchestration |
| Kube YAML | Pod contents, init ordering, runtime settings and secret references | Infrastructure fencing or host policy |
| .kube Quadlet | Binding a workload to user systemd, published ports and dependencies | Database failover decisions |
| systemd | Service ordering, restart, shutdown and boot behavior | PostgreSQL replication correctness |
| Python installer | Single-host dev/server lifecycle and shared workload installation | DR decisions or remote transport |
| Ansible | Multi-host DR, security integration, transport and assertions | A separate workload installer |
| Python tools | Guarded DR, backup and resumable operator stages | A second configuration-management system |
| Podman | Rootless pods, containers, networks, volumes and secrets | Cluster scheduling |

Host network integration uses the shared `.network` Quadlet. Persistent storage
is declared by Kube PVCs; no separate `.volume` Quadlets are needed here.
User lingering enables services to run before interactive login.
`shared-proxy.service` requires and starts after both apps and Keycloak;
each app service depends on its own PostgreSQL and shared Keycloak; Keycloak
depends on `keycloak-postgres.service`; each PostgreSQL also has
its own boot entrypoint to support a database-only host.

Ordering is not readiness. Init-container success, health checks, systemd
restart and installer/Ansible readiness assertions address different conditions.
The PostgreSQL unit applies health-on-failure kill so unhealthy database
containers are replaced through the systemd lifecycle.

## 5. Build and deployment pipeline

```text
Jinja2 manifest templates + values ──► rendered YAML ──┬──► offline bundle
                                                      └──► operations package
Containerfiles ──► OCI image archives ────► offline bundle only
Deployment / recovery code and docs ──────► respective bundles

Both bundles: VERSION + archive checksums
    │
    ▼
Python installer for local clean installation
  Ansible controller and the same Python workload functions for DR
    │
    ▼
Target host(s) ──► .kube ──► systemd ──► Podman
```

The operations package contains playbooks, roles, runtime definitions, Python
and quarantine tools, and runbooks; it contains no OCI image archives.
YAML is rendered into a temporary build directory and packaged. Source checkout
`deploy/runtime/` contains guides; shared `deploy/quadlet/` templates produce target-specific
Quadlets. Tests compare packaged YAML with independent Jinja2 rendering.
Rendering runs on the build host, not the Oracle Linux target. Images and rendered
definitions are delivered offline; target execution does not fetch from a
registry. Both acceptance artifacts must identify the same clean revision.
Checksums establish integrity against the supplied digest, not publisher
identity; organizational artifact signing is not implemented.

The portable module lives in `deploy/installer/todo_installer/` and uses Jinja2
plus the Python standard library. `apps.py` is the single registry of per-app
names; image, secret, workload, lifecycle and cleanup code consume App objects.
Rendering calls the shared `deploy/manifests/*.yaml.j2` templates once per app
using one environment values file, then renders Keycloak and shared-proxy once.
Adding an App entry activates the already-shared template set with no new
files. Its shared workload functions install files
and reload systemd; callers retain responsibility for safe stop/start ordering.
DR Ansible tasks stage controller-side templates/manifests on the target, call
`install-workload`, and preserve the existing change facts. Hardened DR targets
use the existing exact-file trust role for the Python sources.

Development uses the same manifest templates with development values and direct
`podman kube play/down`. Production uses user systemd. These are different
lifecycle owners; development cleanup must not target a production user store.

Clean deployment performs database-administrative role provisioning using
the bootstrap database identity, separately from ordinary schema migration.
These are PostgreSQL privileges, not a privileged/rootful container mode.
The app init container runs the idempotent
migrator using its dedicated credential. Promoted application recovery does
not rerun administrative role bootstrap.

## 6. Network and data flows

| Flow | Address boundary | Purpose |
|---|---|---|
| Browser → nginx | Published host HTTPS endpoint | Assets, API and identity proxy |
| nginx → frontend | todo-app:8080 or notes-app:8080 on rootless network | Static assets |
| nginx → backend | todo-app:8000 or notes-app:8000 on rootless network | API, health and readiness |
| nginx → Keycloak | keycloak:8080 on rootless network | OIDC browser endpoints under /auth |
| Backend → PostgreSQL | Its own app-postgres:5432 | Application queries with restricted DB role |
| Migrator → PostgreSQL | Its own app-postgres:5432 | Schema changes with migration identity |
| Keycloak → PostgreSQL | keycloak-postgres:5432 | Identity persistence in its own database |
| Backend → Keycloak | Internal configured JWKS endpoint | Signing-key retrieval for JWT validation |
| Standby → current primary | Explicit host TCP5432 (todo), 5433 (notes), 5434 (keycloak) publication | Physical replication, one stream per database |

The network resource is declared in `app-network.network`; its runtime name is
`app-network`. Loopback is shared only within a pod. Cross-pod communication
uses Podman DNS, not host IPs. The production workload supplies the shared
proxy ConfigMap with DNS upstreams for frontend, backend and Keycloak.

Internal service HTTP and trusted-LAN replication are not universally
TLS-enforced. The external HTTPS boundary must not be mistaken for encryption
of every internal flow.

## 7. Identity architecture

```text
app.js → auth.js → keycloak-adapter.js → Keycloak SDK / OIDC endpoints
```

Both UIs use init, isAuthenticated, login, logout, getAccessToken and
getUsername. The adapter owns SDK configuration, check-sso, S256 PKCE,
redirects and token refresh. Token-refresh errors propagate rather than
returning a stale token. Tokens are not deliberately persisted by Todo code.

Backend validation is independent of the frontend adapter: it validates JWT
signature, issuer and audience using configured JWKS. In production/acceptance,
both profiles use `https://todo.test:8443/auth/realms/todo`. The realm remains
`todo`; clients `todo-frontend` and `notes-frontend` have separate audiences,
redirect URIs and web origins. The installer creates the additional public
client in existing realms and changes redirect/origin settings only if needed.
Notes discovers the canonical identity origin so both apps use the same login
cookie. A Todo token cannot authorize Notes writes, or vice versa.
JWKS can be fetched through the internal Keycloak address without changing
the profile's public issuer.

Only Keycloak is implemented. Another provider requires a new adapter, provider
configuration, compatible token claims and real browser/integration testing.
An adapter seam is not evidence that Duende or another provider already works.

## 8. Persistence and secrets

| State | Storage / identity | Lifecycle |
|---|---|---|
| Todo database data | todo-postgres-data | Survives app replacement; explicitly replaced only during approved reseed |
| Notes database data | notes-postgres-data | Same lifecycle as Todo data, independent database |
| Keycloak database data | keycloak-postgres-data | Same lifecycle; holds the shared realm |
| nginx CA and leaf-key state | todo-nginx-data | Survives local app recreation; promotion may create a new demo CA |
| Base backups and WAL | todo-postgres-backup, notes-postgres-backup, keycloak-postgres-backup | One per database; separate from live data; still on the same VM |
| Runtime credentials | Host-local Podman secrets | Provisioned and transferred separately from YAML |

Kube YAML declares each persistent volume with a `PersistentVolumeClaim`.
Podman maps `claimName` to the named volume; `volumeMount.mountPath` is the
path inside the container. PostgreSQL data and backup claims specify creation
UID/GID `999:999`; the nginx TLS claim specifies `101:101`. These are container
IDs mapped through rootless Podman, not host IDs. Existing volumes are reused;
creation annotations do not repair existing ownership.

The `.kube` Quadlet owns the workload's user-systemd lifecycle, including boot.
Normal `podman kube down` and systemd stop preserve these PVC volumes; do not
use `--force` or `KubeDownForce=true` for routine shutdown. A separate `.volume`
Quadlet is appropriate only for a necessary host/systemd storage contract beyond
the PVC, such as a separately managed device or mount. None of these
volumes needs one. See the Podman [PVC documentation](https://docs.podman.io/en/latest/markdown/podman-kube-play.1.html)
and [shutdown semantics](https://docs.podman.io/en/latest/markdown/podman-kube-down.1.html).

Standby bootstrap and approved reseed play only the data PVC extracted from the
canonical rendered PostgreSQL YAML of each database before `pg_basebackup`;
they do not start PostgreSQL against an empty directory. Bootstrap still refuses
existing data; reseed still requires all fencing and confirmation gates before
deleting only the three `<database>-postgres-data` volumes. The existing helper ownership/SELinux handoff is preserved.
Backup configuration requires the existing backup volume mounted read-write at
`/var/lib/postgresql/backup` in active PostgreSQL; helpers still mount it at
`/backup`. Runtime roles remove obsolete volume Quadlet files from earlier Kube
installs without deleting named volumes. Uninstall retains tolerant cleanup of
old definitions: normal uninstall preserves database and backup volumes but
removes TLS state; `remove_data=true` additionally removes database data.
The single-host uninstaller continues to refuse DR/backup hosts.

The bootstrap/admin, migrator, application, Keycloak and replication
identities have different jobs. The Python installer constructs Kube-compatible secret
objects from existing secrets in memory. Secret values do not belong in manifest
templates, rendered YAML, Git or transcripts. Sensitive transfer tasks use SSH
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

The DR flow covers one group of three databases: Todo, Notes and Keycloak
(`apps.REPLICATED_DATABASES`). Bootstrap, promotion, backup, rebuild and status
always act on the complete group; Ansible refuses a partial application
override. Each database has its own replication port, slot, replication
credential, WAL archive and backup volume. Promotion first checks every
database and records a durable decision; a failure after the first database is
promoted leaves a partial record that blocks blind retry. PostgreSQL cannot
promote independent instances atomically, so this is a fail-closed gate, not
an atomic group switch.

Initially one host serves both applications and a second streams each
PostgreSQL database's WAL asynchronously. Physical slots retain needed WAL within a configured bound;
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

Complete unchanged-revision Oracle Linux acceptance passed for earlier
Todo-only topologies: 688a0f6 and 12c3bef (evidence-grade) and 9e54cfb
(process-level). See the run records for observations and exact scope. The
seven-pod, three-database group has passed phased checkpoints on disposable
Fedora VMs ([multi-app DR verification](MULTI-APP-DR-VERIFICATION.md)) but has
not yet passed a full unchanged-revision two-VM acceptance.
See [runtime results](../deploy/runtime/RESULTS.md). Static tests or a green CI
run do not replace the full two-VM test. The Python installer extraction has
unit, real rendering, package and Ansible transport coverage; it still requires
a new unchanged-revision Oracle Linux DR acceptance run. The multi-app
single-host implementation was separately exercised in a disposable Fedora 44
VM, Podman 5.8.1, rootless and SELinux enforcing: dev/server, actual offline OCI
loading without Helm, persistence, unchanged repeats, exact systemd SourcePaths,
trusted SAN TLS and real browser SSO/CRUD/audience isolation. This test did not
access the existing acceptance VMs and is not a DR acceptance.

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
| Which definitions implement the pods? | [Kube runtime](../deploy/runtime/README.md) |
| How is replication arranged? | [Standby architecture](../deploy/ansible/STANDBY-ARCHITECTURE.md) |
| How do I run acceptance safely? | [Acceptance sequence and criteria](ACCEPTANCE.md) |
| How does old-primary isolation work? | [Quarantine](PROXMOX-QUARANTINE.md) |
| How do backup and reseeding work? | [Backup/PITR](../deploy/ansible/BACKUP-PITR.md), [restore redundancy](../deploy/ansible/RESTORE-REDUNDANCY.md) |
| How are security details handled? | [SELinux](SELINUX.md), [secrets](SECRETS.md), [TLS](TLS.md) |

Source paths identify implementation, not a second source of configuration.
When code and this overview diverge, verify the implementation and correct the
document; never use prose alone as authorization for a destructive operation.
