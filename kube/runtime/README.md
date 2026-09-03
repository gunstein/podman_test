# Canonical Podman Kube application tier

These manifests are the production-shaped replacement candidates for the
accepted per-container Quadlets. They make the lifecycle boundaries explicit:
frontend and backend share one application pod, while PostgreSQL and Keycloak
remain independent shared services on the user-defined network. PostgreSQL
migration remains a separate safety gate
from the application tier and from the disaster-recovery workflows.

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
app.yaml                 -> migration init, backend and frontend
keycloak.yaml            -> independent identity service
postgres.yaml            -> independent database and persistent volumes
config-dev.yaml          -> shared development configuration
todo-app.kube            -> application pod systemd lifecycle
todo-keycloak.kube       -> identity pod systemd lifecycle
todo-postgres.kube       -> database pod systemd lifecycle
../../quadlet/todo.network -> shared rootless network
```

Development supplies `config-dev.yaml` directly to `podman kube play`.
Production installs the same workload YAML beside a generated
`config-runtime.yaml` and wraps it with the matching `.kube` unit.

Podman names a regular container from its pod and container entries. The grouped
containers therefore become `todo-app-backend` and `todo-app-frontend`, owned
by one `todo-app.service`. Nginx reaches its colocated backend on
`127.0.0.1:8000`; both containers reach the independent `todo-postgres` and
`todo-keycloak` pods through `todo.network`.

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

Secret values are never stored in these files. The controlled Ansible
migrations construct the objects from the existing raw Podman secrets.

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

Its pod and container are both named `todo-postgres`, and its `.kube` unit
passes `--no-pod-prefix` so the
existing DR, backup and Ansible commands retain the exact container name. This
requires the tested Podman 5.8.2 platform.

Its `.kube` unit also applies `--health-on-failure=kill` after each creation.
This preserves the accepted health failure contract: Podman terminates a
persistently unhealthy database container and systemd recreates the workload.

The application migration is intentionally separate from PostgreSQL. Run it
only through `ansible/migrate-application-to-kube.yml`, with the exact
confirmation documented by that playbook. It backs up the three installed
`.container` files before replacing their generated units. The rollback
playbook restores those files without changing database or TLS data.

The current-primary database migration has its own confirmation, rollback and
acceptance gate. It reuses `todo-postgres-data` and `todo-postgres-backup`,
compares the database system identifier, verifies replication and forces an
exact WAL archive check before the application is restarted. Follow
`ansible/POSTGRES-KUBE-MIGRATION.md`; do not use it during failover.

Direct development requires the three application Kube-compatible secrets, an
already running `todo-postgres` database and the shared `todo-network`. Start
Keycloak first, then start the grouped application workload with the same YAML
used in production:

```bash
podman kube play \
  --network todo-network \
  --configmap kube/runtime/config-dev.yaml \
  kube/runtime/keycloak.yaml

podman kube play \
  --network todo-network \
  --configmap kube/runtime/config-dev.yaml \
  kube/runtime/app.yaml
```

Remove it with:

```bash
podman kube play --down kube/runtime/app.yaml
podman kube play --down kube/runtime/keycloak.yaml
```

On a separate development host, PostgreSQL can use the same workload:

```bash
podman kube play \
  --no-pod-prefix \
  --network todo-network \
  --configmap kube/runtime/config-dev.yaml \
  kube/runtime/postgres.yaml
```

Never run that development command against a production user's container store:
the canonical manifest deliberately names the persistent production volumes.

The grouped app cannot start unless its migration init succeeds. Direct mode is
a developer lifecycle; user systemd owns dependency, restart and boot behavior
in the deployed environment.
