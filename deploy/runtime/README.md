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
app-network
  shared-proxy (nginx, one SAN certificate)
    ├── todo-app  ── todo-postgres (Todo + Keycloak schema)
    ├── notes-app ── notes-postgres (Notes only)
    └── keycloak (shared todo realm)
```

| Pod | User service | Long-running containers |
|---|---|---|
| `todo-app` | `todo-app.service` | `todo-backend`, `todo-frontend` |
| `notes-app` | `notes-app.service` | `notes-backend`, `notes-frontend` |
| `notes-postgres` | `notes-postgres.service` | `notes-postgres` |
| `keycloak` | `keycloak.service` | `keycloak` |
| `todo-postgres` | `todo-postgres.service` | `todo-postgres` |
| `shared-proxy` | `shared-proxy.service` | `nginx` |

The source definitions, relative to the repository root, are:

```text
deploy/manifests/postgres.yaml.j2       shared database Pod and PVCs (todo, notes, keycloak)
deploy/manifests/postgres-config.yaml.j2 shared database ConfigMap
deploy/manifests/app.yaml.j2            shared app Pod (todo, notes)
deploy/manifests/app-config.yaml.j2     shared app backend ConfigMap
deploy/manifests/keycloak.yaml.j2       shared identity Pod and ConfigMap
deploy/manifests/shared-proxy.yaml.j2   independent proxy and ConfigMaps
deploy/environments/{local,prod}/values.yaml  non-secret environment overrides
deploy/quadlet/*.kube.j2               six shared systemd workload templates
deploy/quadlet/app-network.network           shared rootless network
```

Jinja2 is a build-time renderer, not a runtime orchestrator. Production rendering
writes `app.yaml`, `keycloak.yaml`, `postgres.yaml`, `config.yaml` and
`shared-proxy.yaml`, plus `notes-app.yaml`, `notes-postgres.yaml`,
`notes-config.yaml`, `keycloak-postgres.yaml` and `keycloak-config.yaml` under
`generated/kube-runtime/` by default. Development uses
`generated/dev/`; both output directories are ignored by Git. This directory
contains documentation only.

Packages contain freshly rendered YAML under `generated/kube-runtime/` and the
same six source Quadlet templates under `deploy/quadlet/`. The Python installer renders the
target-specific `.kube` files and installs them beside the workload YAML under
`~/.config/containers/systemd/todo-kube-runtime/`. Targets do not render Kube YAML at all.
CI compares actual package contents against fresh rendering.

All six `.kube` units use `--no-pod-prefix`, so the grouped containers keep
the stable names `todo-backend` and `todo-frontend` while one
`todo-app.service` owns their shared lifecycle. The separate `shared-proxy.service` owns container `nginx`, terminates TLS using
`todo-nginx-data`, and routes to `todo-app:8080` (frontend), `todo-app:8000`
(backend), the corresponding `notes-app` ports, and `keycloak:8080`. The frontend is HTTP-only; no TLS material
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

The workloads expect seven externally provisioned Kube-compatible Podman
secrets:

- `todo-kube-backend-secret`, containing `database-password`;
- `todo-kube-migrator-secret`, containing the separate migrator
  `database-password`;
- `todo-kube-keycloak-secret`, containing `database-password` and
  `bootstrap-admin-password`;
- `todo-kube-postgres-secret`, containing `database-password`;
- `notes-kube-backend-secret`, `notes-kube-migrator-secret` and
  `notes-kube-postgres-secret`, each containing its own `database-password`.

Raw credentials are separate for both apps. Shared identity retains the
`todo-keycloak-*` secret names and Todo database schema; it is not a second
identity installation. Notes uses `notes_migrator` and `notes_app` DB roles.

Secret values are never stored in manifest templates or rendered YAML. The shared
Python installer constructs these Kube-compatible objects in memory from
the host-local raw Podman secrets.

## Operational resilience

The core relationship is proxy, app, identity, database, network, persistence and
external secrets. Replication, WAL archiving, backup, PITR, promotion and
standby rebuild are a separate **Todo-only** operational layer built around
that core. Notes DR is a dedicated follow-up phase. Its backup PVC reserves
storage but does not implement a backup policy or protected recovery flow.

The PostgreSQL workload deliberately carries two Todo-specific resilience
details which are not required for a basic PostgreSQL Kube workload: the
`todo-postgres-backup` claim/mount preserves the existing physical backup and
WAL archive, and `max_slot_wal_keep_size=1GB` bounds WAL retained for the
physical replication slot. A minimal educational workload would keep only the
data claim; this runtime keeps both details to preserve the validated backup and
replication contracts.

All six `.kube` units pass `--no-pod-prefix`. PostgreSQL therefore retains
the exact `todo-postgres` container name used by DR and backup commands, while
the grouped app retains stable `todo-migrate`, `todo-backend` and
`todo-frontend` names for verification. Export the public CA from `nginx` only. The
Oracle Linux DR baseline is Podman 5.8.2; six-pod single-host tests also passed
on Fedora 44 with Podman 5.8.1. The installer requires `--no-pod-prefix`.

Its `.kube` unit also applies `--health-on-failure=kill` after each creation.
This preserves the accepted health failure contract: Podman terminates a
persistently unhealthy database container and systemd recreates the workload.

The historical per-container migration and rollback tools were retired after
acceptance of 688a0f6. They remain recoverable from Git history; normal recovery
uses the active DR runbooks, not runtime-format migration.

Direct development provisions the seven Kube-compatible Podman secrets from
host-local raw secrets. Install Python 3.9+ and Jinja2 first. Render
and start the six workloads with:

```bash
deploy/scripts/dev-up.sh
```

Map both `todo.test` and `notes.test` to the serving host and trust one shared
CA; see [TLS instructions](../../docs/TLS.md). Both apps use the same `todo`
realm, separate clients, and a single SAN certificate.

The shell entry points are thin wrappers around `python3 -m todo_installer`.
The installer invokes the existing renderer with local values, prepares images
and external secrets, then performs ordered `podman kube play` calls. Every
missing password, bootstrap/admin included, is generated in code and never
prompted or printed. It provisions database roles before
Keycloak/app startup and reapplies grants after proxy startup. Stop the workloads in reverse order with:

```bash
deploy/scripts/dev-down.sh
```

Never run that development command against a production user's container store:
the canonical manifest deliberately names the persistent production volumes.

The grouped app cannot start unless its migration init succeeds. Direct mode is
a developer lifecycle; user systemd owns dependency, restart and boot behavior
in the deployed environment.
