# Canonical Podman Kube runtime

This is the final application runtime and the normal clean-install target.
The manifests make the lifecycle boundaries explicit:
frontend and backend share one application pod, while PostgreSQL and Keycloak
remain independent shared services on the user-defined network. PostgreSQL
replication, backup and disaster recovery remain separate operational layers.

## Core architecture

```text
                         todo.network
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
          todo-app       todo-keycloak    todo-postgres
     +---------------+        pod              pod
     | migrate init  |                           |
     | backend       |                           +-- persistent data
     | frontend      |
     +---------------+
```

The files that define this architecture are:

```text
../../helm/todo/templates -> source workload and ConfigMap templates
../../helm/todo/values-*.yaml -> non-secret environment values
app.yaml                 -> rendered migration init, backend and frontend
keycloak.yaml            -> rendered independent identity service
postgres.yaml            -> rendered database and persistent volumes
config.yaml              -> rendered non-secret production configuration
todo-app.kube            -> application pod systemd lifecycle
todo-keycloak.kube       -> identity pod systemd lifecycle
todo-postgres.kube       -> database pod systemd lifecycle
../../quadlet/todo.network -> shared rootless network
```

Helm is a build-time renderer, not a runtime orchestrator. The checked-in YAML
is rendered from `values-prod.yaml` and its drift is checked in CI. The
offline bundle therefore needs no Helm binary on Oracle Linux. Development
renders the same chart with `values-dev.yaml`; production installs the
rendered YAML beside the matching `.kube` units.

All three `.kube` units use `--no-pod-prefix`, so the grouped containers keep
the stable names `todo-backend` and `todo-frontend` while one
`todo-app.service` owns their shared lifecycle. Nginx reaches its colocated
backend on `127.0.0.1:8000`; both containers reach the independent
`todo-postgres` and `todo-keycloak` pods through `todo.network`.

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

Secret values are never stored in Helm values or rendered YAML. The clean
Ansible deployment constructs these Kube-compatible objects in memory from
the host-local raw Podman secrets.

## Operational resilience

The core relationship is only app, identity, database, network, persistence and
external secrets. Replication, WAL archiving, backup, PITR, promotion and
standby rebuild are a separate operational layer built around that core.

The PostgreSQL workload deliberately carries two Todo-specific resilience
details which are not required for a basic PostgreSQL Kube workload: the
`todo-postgres-backup` claim/mount preserves the existing physical backup and
WAL archive, and `max_slot_wal_keep_size=1GB` bounds WAL retained for the
physical replication slot. A minimal educational workload would keep only the
data claim; this production-shaped candidate keeps both details so migration
does not weaken the already validated backup and replication contracts.

All three `.kube` units pass `--no-pod-prefix`. PostgreSQL therefore retains
the exact `todo-postgres` container name used by DR and backup commands, while
the grouped app retains stable `todo-migrate`, `todo-backend` and
`todo-frontend` names for verification and certificate export. This
requires the tested Podman 5.8.2 platform.

Its `.kube` unit also applies `--health-on-failure=kill` after each creation.
This preserves the accepted health failure contract: Podman terminates a
persistently unhealthy database container and systemd recreates the workload.

The old application migration is temporary transition and rollback evidence
for hosts running the tagged per-container reference. Clean install never uses
it. Run it only through `ansible/migrate-application-to-kube.yml`, with the
exact confirmation documented by that playbook. It backs up the three installed
`.container` files before replacing their generated units. The rollback
playbook restores those files without changing database or TLS data.

The current-primary database migration has its own confirmation, rollback and
acceptance gate. It reuses `todo-postgres-data` and `todo-postgres-backup`,
compares the database system identifier, verifies replication and forces an
exact WAL archive check before the application is restarted. Follow
`ansible/POSTGRES-KUBE-MIGRATION.md`; do not use it during failover.

Direct development requires the four Kube-compatible Podman secrets. Render
and start the three workloads with:

```bash
scripts/dev-up.sh
```

The wrapper contains only `helm template`, network/secret checks and ordered
`podman kube play` calls. Stop the workloads in reverse order with:

```bash
scripts/dev-down.sh
```

Never run that development command against a production user's container store:
the canonical manifest deliberately names the persistent production volumes.

The grouped app cannot start unless its migration init succeeds. Direct mode is
a developer lifecycle; user systemd owns dependency, restart and boot behavior
in the deployed environment.
