# Canonical Podman Kube application tier

These manifests are the production-shaped replacement candidates for the
accepted per-container Quadlets. They preserve the existing pod DNS names and
systemd service names. PostgreSQL migration remains a separate safety gate
from the application tier and from the disaster-recovery workflows.

The workload files are shared:

```text
backend.yaml   -> todo-backend pod
keycloak.yaml  -> todo-keycloak pod
frontend.yaml  -> todo-frontend pod and todo-nginx-data PVC
postgres.yaml  -> todo-postgres pod, data PVC and backup PVC
```

Development supplies `config-dev.yaml` directly to `podman kube play`.
Production installs the same workload YAML beside a generated
`config-runtime.yaml` and wraps it with the matching `.kube` unit.

Podman names a container from its pod and container entries. For example, the
backend container becomes `todo-backend-backend`. DNS and systemd retain the
stable names `todo-backend` and `todo-backend.service`.

The PostgreSQL workload is the deliberate exception. Its pod and container are
both named `todo-postgres`, and its `.kube` unit passes `--no-pod-prefix` so the
existing DR, backup and Ansible commands retain the exact container name. This
requires the tested Podman 5.8.2 platform.

Its `.kube` unit also applies `--health-on-failure=kill` after each creation.
This preserves the accepted health failure contract: Podman terminates a
persistently unhealthy database container and systemd recreates the workload.

The workloads expect two externally provisioned Kube-compatible Podman
secrets:

- `todo-kube-backend-secret`, containing `database-password`;
- `todo-kube-keycloak-secret`, containing `database-password` and
  `bootstrap-admin-password`.
- `todo-kube-postgres-secret`, containing `database-password`.

Secret values are never stored in these files. The controlled Ansible
migrations construct the objects from the existing raw Podman secrets.

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

Direct development requires an already running `todo-postgres` database, the
shared `todo-network` and the two Kube-compatible secrets. Start each workload
with the same YAML used in production, for example:

```bash
podman kube play \
  --network todo-network \
  --configmap kube/runtime/config-dev.yaml \
  kube/runtime/backend.yaml
```

Remove it with:

```bash
podman kube play --down kube/runtime/backend.yaml
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

Start Keycloak after the backend and start the frontend last. Direct mode is a
developer lifecycle; user systemd owns restart and boot behavior in the
deployed environment.
