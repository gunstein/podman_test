# Learning guide

This guide teaches the current rootless Podman Kube implementation, not the
historical per-container model. The source repository retains
`docs/legacy/LEARNING-GUIDE.md` for comparison with `quadlet-reference-v1`;
historical guides are intentionally not distributed in deployment bundles.
Functional DR has been demonstrated with repairs; unchanged-revision Oracle
Linux acceptance remains pending. See [acceptance](MANUAL-DR-QUICKSTART.md).

For the authoritative system overview and design boundaries, read
[System architecture](ARCHITECTURE.md). This guide focuses on learning exercises.

## 1. Follow the definition to the running service

```text
helm/todo + values             build host only
        ↓ render
kube/runtime/*.yaml            reviewed, packaged workload definitions
        ↓ referenced by
kube/runtime/*.kube            Quadlet lifecycle and host integration
        ↓ generator
systemd user services         ordering, restart, boot and stop
        ↓
rootless Podman                executes pods and containers
```

Helm is a build-time template tool, not an installed target-host dependency.
CI compares rendering with checked-in YAML. This is the Podman-supported
subset of Kubernetes YAML: no Kubernetes cluster or portability promise.
Read `helm/todo/templates/`, `helm/todo/values-prod.yaml`,
`scripts/render-kube-runtime.sh` and `kube/runtime/README.md`.
To experiment, render into a temporary directory, never over deployed state:

```bash
render_dir=$(mktemp -d)
scripts/render-kube-runtime.sh helm/todo/values-prod.yaml "$render_dir"
diff -u kube/runtime/app.yaml "$render_dir/app.yaml"
```

This experiment requires Helm on the build host. The remaining observation
commands run as the service user on an installed guest unless stated otherwise.

## 2. Group by lifecycle, not by application name

| Workload / service | Contents | Why separate? |
|---|---|---|
| `todo-postgres.service` | PostgreSQL | Data, replication and recovery outlive app deploys |
| `todo-keycloak.service` | Keycloak | Identity has its own startup and health lifecycle |
| `todo-app.service` | Migration init container, backend, nginx frontend | Migration gates startup; backend and proxy share app lifecycle |

Read `kube/runtime/app.yaml`, `keycloak.yaml`, `postgres.yaml` and their
`.kube` units. nginx reaches backend on loopback inside the app pod.
Independent pods use `todo-network` DNS names `todo-postgres` and
`todo-keycloak`. A shared pod is not a reason to put every dependency in it.

```bash
podman pod ps
podman ps --format 'table {{.Names}}\t{{.Status}}'
podman network inspect todo-network
```

## 3. Learn development and production lifecycle separately

Development uses direct `podman kube play/down` through `scripts/dev-up.sh`
and `scripts/dev-down.sh`; read their cleanup scope before running them.
Production uses generated user services from `.kube` Quadlets.
`todo-app.service` requires PostgreSQL and Keycloak; PostgreSQL is also a
boot entrypoint so database-only standby can run independently.
Do not manually enable generated services. Lingering allows boot before login.

```bash
systemctl --user list-dependencies todo-app.service
systemctl --user cat todo-postgres.service
systemctl --user show todo-app.service -p ActiveState -p NRestarts
loginctl show-user "$USER" -p Linger
```

Ordering is not readiness. Init migration, container health checks, systemd
restart and Ansible readiness checks have different responsibilities.
See `kube/runtime/RESULTS.md` for demonstrated behavior and pending gates.

## 4. Images, rootless storage and external secrets

Containerfiles provide immutable app content. PostgreSQL data, nginx TLS state
and backup each use persistent volumes with different lifecycles.
Root in a rootless container is not host root; subordinate IDs map ownership.
SELinux `:z` sharing and `:Z` private labels differ from `:U` ownership.

```bash
podman info --format 'Rootless={{.Host.Security.Rootless}}'
podman unshare cat /proc/self/uid_map
podman volume ls
podman secret ls
```

Read [SELinux](SELINUX.md) and [secrets](SECRETS.md).
Secrets are external host-local Podman objects; workload YAML carries
references, not plaintext values. Do not dump container environments or secret
payloads into an ordinary transcript. Ansible transfers required values through
protected memory/SSH with `no_log` and checks credential equality before reseed.

## 5. Database provisioning is not schema migration

Host provisioning establishes database roles and grants with separate
bootstrap, migrator, application, Keycloak and replication identities.
The app pod's init container applies schema migrations before serving traffic.
Ordinary app recovery does not repeat administrative role bootstrap.
Read `backend/`, `ansible/roles/application_kube_runtime/` and
`ansible/roles/promoted_application/`.

## 6. Browser, nginx, TLS and identity

nginx serves plain HTML/CSS/JavaScript, proxies API and identity traffic and
terminates HTTPS. OpenSSL provides a local demo CA, not managed production PKI.
Client CA trust and server private-key protection are separate obligations.

```bash
podman exec todo-frontend nginx -t
curl --fail https://todo.test:8443/ready
curl --fail https://todo.test:8443/auth/realms/todo/.well-known/openid-configuration
```

These HTTPS commands require client name resolution and CA trust.
Read `frontend/nginx.conf`, [TLS](TLS.md) and the frontend adapter chain:

```text
app.js → auth.js → keycloak-adapter.js → Keycloak SDK
```

Todo depends on init, authentication state, login/logout, access-token retrieval
and username. The adapter preserves check-sso, S256 PKCE and token refresh.
Only Keycloak is implemented; another provider requires a new adapter,
provider configuration and browser validation, not merely an issuer change.
Backend validation uses configured issuer, JWKS and audience independently.

## 7. Ansible, offline artifacts and host security

Ansible installs and verifies state; systemd remains responsible afterwards.
Both offline bundles must identify one clean revision in VERSION and pass
archive checksums before extraction. Targets consume rendered YAML and OCI
archives without Helm or registry access. Read `offline/README.md`.

SELinux, Unix ownership, user namespaces, fapolicyd and firewalld are independent
layers. The exact-file trust role waits boundedly for canonical path, size and
SHA-256 in fapolicyd's active database. Quarantine installer restores existing
SELinux policy after atomic file replacement; new QGA permissions need opt-in.

```bash
getenforce
systemctl is-active fapolicyd firewalld
```

Read `ansible/roles/todo_fapolicyd/`, `offline/FAPOLICYD.md` and
[quarantine](PROXMOX-QUARANTINE.md). Do not disable security to diagnose failures.

## 8. Recovery is part of the architecture

Replication improves availability but also copies logical mistakes.
Fencing precedes promotion; an unreachable endpoint alone is not fencing.
Promotion restores availability, rebuild restores redundancy, and a later
switchover back to the original host is a separate operation.
Async replication cannot guarantee receipt of commits never sent to standby.

Backup/PITR requires a verified base backup and continuous archived WAL.
The disposable restore has no network and never targets the live volume.
On-VM backup does not protect against loss of that VM or its host.

For observation on the configured standby / promoted primary respectively:

```bash
python3 /opt/todo/bin/todo_dr.py status
python3 /opt/todo/bin/todo_backup.py status
```

Follow [the operator checklist](MANUAL-DR-QUICKSTART.md) for the correct machine
and phase; not every command applies to every role. It links the detailed
promotion, backup and rebuild runbooks and defines explicit destructive gates.
Never run a reset, promotion or rebuild merely as an exploratory learning step.
