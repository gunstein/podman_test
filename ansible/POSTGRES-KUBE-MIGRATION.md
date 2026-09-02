# PostgreSQL primary Podman Kube migration

This is a controlled migration of the current writable PostgreSQL primary from
its accepted per-container Quadlet to the shared `kube/runtime/postgres.yaml`
workload. It is not a failover operation and must not run during an incident.

The migration preserves these operational names:

```text
todo-postgres.service
todo-postgres pod
todo-postgres container
```

The `.kube` unit passes Podman's supported `--no-pod-prefix` option. The
accepted platform is therefore pinned to rootless Podman 5.8.2; older Podman
versions without that option cannot use this migration.

The Kube liveness probe supplies the healthcheck, while an `ExecStartPost`
command sets `--health-on-failure=kill` on the stable container. Together with
`ExitCodePropagation=any` and systemd `Restart=on-failure`, a persistently
unhealthy PostgreSQL process causes the complete Kube workload to be recreated.
The migration verifies that the effective failure action is `kill`.

## Safety boundary

The playbook requires:

- the exact current-primary hostname, address and confirmation;
- active PostgreSQL, backend, Keycloak and frontend services;
- a writable database with WAL archiving enabled and healthy;
- an asynchronous rebuilt standby caught up with zero-byte lag;
- the existing data and backup volumes, PostgreSQL image and raw database
  secret;
- all three installed database container/volume Quadlets; and
- no partial Kube database runtime directory.

Before stopping anything, it records the database system identifier, builds a
Kube-compatible Podman secret in memory, and refreshes the rollback copy of the
installed `todo-postgres.container`. Secret values are never written to a
regular file.

The application tier is stopped before PostgreSQL. The data and backup volume
Quadlets remain installed and neither volume is removed. After replacement,
the playbook requires the same database system identifier, writable/archive
state, stable pod/container name, zero-byte replication lag and an exact newly
archived WAL segment before restarting the application.

## Run

Use the role-based recovery inventory from the current primary:

```bash
ansible-playbook \
  --syntax-check \
  --inventory ansible/inventory-recovery.ini \
  ansible/migrate-postgres-primary-to-kube.yml

ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/migrate-postgres-primary-to-kube.yml \
  --extra-vars \
  '{"todo_confirm_postgres_kube_migration":"todo-standby"}'
```

Replace `todo-standby` with the exact `todo_current_primary` inventory host.

Expected source after migration:

```bash
systemctl --user show \
  todo-postgres.service \
  --property=SourcePath
```

The path must end in:

```text
todo-kube-database-runtime/todo-postgres.kube
```

Existing operational commands remain valid:

```bash
podman inspect todo-postgres
podman exec todo-postgres pg_isready --username todo --dbname todo
python3 "$HOME/.config/todo/todo_backup.py" status
```

Verify public HTTPS, browser E2E, reboot, replication and backup/PITR behavior
before treating this candidate as accepted.

## Roll back

Use the dedicated rollback while the current primary is still correctly
identified in the recovery inventory:

```bash
ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/rollback-postgres-primary-to-container-quadlet.yml \
  --extra-vars \
  '{"todo_confirm_postgres_quadlet_rollback":"todo-standby"}'
```

Rollback requires both the installed Kube unit and preserved container
Quadlet before stopping anything. It removes only the Kube runtime and derived
database secret, restores the exact saved `.container`, and rechecks database
identity, archiving, replication and application readiness. It never deletes
or recreates `todo-postgres-data` or `todo-postgres-backup`.
