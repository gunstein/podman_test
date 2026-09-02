# Canonical Podman Kube application tier

These manifests are the first production-shaped replacement for the accepted
per-container Quadlets. They preserve the existing pod DNS names and systemd
service names while leaving the accepted PostgreSQL service, volume,
replication and disaster-recovery tooling unchanged.

The workload files are shared:

```text
backend.yaml   -> todo-backend pod
keycloak.yaml  -> todo-keycloak pod
frontend.yaml  -> todo-frontend pod and todo-nginx-data PVC
```

Development supplies `config-dev.yaml` directly to `podman kube play`.
Production installs the same workload YAML beside a generated
`config-runtime.yaml` and wraps it with the matching `.kube` unit.

Podman names a container from its pod and container entries. For example, the
backend container becomes `todo-backend-backend`. DNS and systemd retain the
stable names `todo-backend` and `todo-backend.service`.

The workloads expect two externally provisioned Kube-compatible Podman
secrets:

- `todo-kube-backend-secret`, containing `database-password`;
- `todo-kube-keycloak-secret`, containing `database-password` and
  `bootstrap-admin-password`.

Secret values are never stored in these files. The controlled Ansible
migration constructs those two objects from the existing raw Podman secrets.

The application migration is intentionally separate from PostgreSQL. Run it
only through `ansible/migrate-application-to-kube.yml`, with the exact
confirmation documented by that playbook. It backs up the three installed
`.container` files before replacing their generated units. The rollback
playbook restores those files without changing database or TLS data.

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

Start Keycloak after the backend and start the frontend last. Direct mode is a
developer lifecycle; user systemd owns restart and boot behavior in the
deployed environment.
