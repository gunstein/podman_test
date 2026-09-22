# Canonical Podman Kube runtime

This is the final application runtime and the normal clean-install target.
The manifests make the lifecycle boundaries explicit:
frontend and backend share one application pod, while PostgreSQL and Keycloak
and the shared nginx proxy remain independent services on the user-defined network. PostgreSQL
replication, backup and disaster recovery remain separate operational layers.

For the whole-system design and responsibilities, see
[System architecture](../../docs/ARCHITECTURE.md). This guide focuses on the
canonical runtime files and their lifecycle contracts.

## Core architecture

```text
                         todo.network
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
 shared-proxy → todo-app  todo-keycloak    todo-postgres
     +---------------+        pod              pod
     | migrate init  |                           |
     | backend       |                           +-- persistent data
     | frontend      |
     +---------------+
```

| Pod | User service | Long-running containers |
|---|---|---|
| `todo-app` | `todo-app.service` | `todo-backend`, `todo-frontend` |
| `todo-keycloak` | `todo-keycloak.service` | `todo-keycloak` |
| `todo-postgres` | `todo-postgres.service` | `todo-postgres` |
| `shared-proxy` | `shared-proxy.service` | `nginx` |

The source definitions, relative to the repository root, are:

```text
deploy/charts/todo/templates/          app, identity, database and ConfigMap
deploy/charts/shared-proxy/templates/  independent proxy and ConfigMaps
deploy/environments/{local,prod}/values.yaml  non-secret environment overrides
deploy/quadlet/*.kube.j2               four shared systemd workload templates
deploy/quadlet/todo.network           shared rootless network
```

Helm is a build-time renderer, not a runtime orchestrator. Production rendering
writes `app.yaml`, `keycloak.yaml`, `postgres.yaml`, `config.yaml` and
`shared-proxy.yaml` under `generated/kube-runtime/` by default. Development uses
`generated/dev/`; both output directories are ignored by Git. This directory
contains documentation only.

Packages contain freshly rendered YAML under `generated/kube-runtime/` and the
same four source Quadlet templates under `deploy/quadlet/`. The Python installer renders the
target-specific `.kube` files and installs them beside the workload YAML under
`~/.config/containers/systemd/todo-kube-runtime/`. Targets do not need Helm.
CI compares actual package contents against fresh rendering.

All four `.kube` units use `--no-pod-prefix`, so the grouped containers keep
the stable names `todo-backend` and `todo-frontend` while one
`todo-app.service` owns their shared lifecycle. The separate `shared-proxy.service` owns container `nginx`, terminates TLS using
`todo-nginx-data`, and routes to `todo-app:8080` (frontend), `todo-app:8000`
(backend), and `todo-keycloak:8080`. The frontend is HTTP-only; no TLS material
belongs in `todo-frontend`. App containers share loopback, but the proxy does not.

The `migrate` init container runs
`python -m backend.migrate --connect-timeout 120 up` before either regular
application container starts. It retries only transient connection failures
while PostgreSQL is unavailable or still starting. Authentication and SQL
errors fail immediately. Podman creates Kube init containers as type `once`: a failed
or timed-out migration prevents the app pod from starting, while a successful
init container is removed after it completes. The migrations are
idempotent, but database role bootstrap and grants remain separate operational
steps.

The workloads expect four externally provisioned Kube-compatible Podman
secrets:

- `todo-kube-backend-secret`, containing `database-password`;
- `todo-kube-migrator-secret`, containing the separate migrator
  `database-password`;
- `todo-kube-keycloak-secret`, containing `database-password` and
  `bootstrap-admin-password`;
- `todo-kube-postgres-secret`, containing `database-password`.

Secret values are never stored in Helm values or rendered YAML. The shared
Python installer constructs these Kube-compatible objects in memory from
the host-local raw Podman secrets.

## Operational resilience

The core relationship is proxy, app, identity, database, network, persistence and
external secrets. Replication, WAL archiving, backup, PITR, promotion and
standby rebuild are a separate operational layer built around that core.

The PostgreSQL workload deliberately carries two Todo-specific resilience
details which are not required for a basic PostgreSQL Kube workload: the
`todo-postgres-backup` claim/mount preserves the existing physical backup and
WAL archive, and `max_slot_wal_keep_size=1GB` bounds WAL retained for the
physical replication slot. A minimal educational workload would keep only the
data claim; this runtime keeps both details to preserve the validated backup and
replication contracts.

All four `.kube` units pass `--no-pod-prefix`. PostgreSQL therefore retains
the exact `todo-postgres` container name used by DR and backup commands, while
the grouped app retains stable `todo-migrate`, `todo-backend` and
`todo-frontend` names for verification. Export the public CA from `nginx` only. This
requires the tested Podman 5.8.2 platform.

Its `.kube` unit also applies `--health-on-failure=kill` after each creation.
This preserves the accepted health failure contract: Podman terminates a
persistently unhealthy database container and systemd recreates the workload.

The historical per-container migration and rollback tools were retired after
acceptance of 688a0f6. They remain recoverable from Git history; normal recovery
uses the active DR runbooks, not runtime-format migration.

Direct development provisions the four Kube-compatible Podman secrets from
host-local raw secrets. Install Python 3.9+ and Jinja2 first. Render
and start the four workloads with:

```bash
deploy/scripts/dev-up.sh
```

The shell entry points are thin wrappers around `python3 -m todo_installer`.
The installer invokes the existing renderer with local values, prepares images
and external secrets, then performs ordered `podman kube play` calls. Missing
bootstrap/admin passwords require an interactive terminal; runtime-role
passwords are generated only when missing. It provisions database roles before
Keycloak/app startup and reapplies grants after proxy startup. Stop the workloads in reverse order with:

```bash
deploy/scripts/dev-down.sh
```

Never run that development command against a production user's container store:
the canonical manifest deliberately names the persistent production volumes.

The grouped app cannot start unless its migration init succeeds. Direct mode is
a developer lifecycle; user systemd owns dependency, restart and boot behavior
in the deployed environment.
