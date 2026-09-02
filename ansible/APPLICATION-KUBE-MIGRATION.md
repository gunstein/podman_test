# Application-tier Podman Kube migration

Use this controlled migration only on the current writable primary after the
isolated backend, Keycloak, nginx, PostgreSQL and replication Kube gates have
passed. It replaces the backend, Keycloak and frontend runtime definitions.
PostgreSQL, its container, systemd unit, volumes, replication, WAL archive and
DR tools remain unchanged.

## Preconditions

- Use the role-based recovery inventory from the current primary.
- The current host name and `todo_node_address` must identify that host.
- `todo-postgres.service` must be active and the database must report
  `f|off` for recovery and read-only state.
- The three application images and existing raw Podman secrets must exist.
- All three installed per-container application Quadlets must still exist.
- Keep the client mapping for `todo.test` on the current primary.

The migration creates no plaintext secret file. It reads the existing raw
secrets with `no_log`, constructs the Kube Secret objects in memory and sends
them directly to `podman secret create` over stdin.

## Preserve pre-migration evidence

Record the database identity and TLS identity before migration:

```bash
podman exec todo-postgres \
  psql --username todo --dbname postgres --tuples-only --no-align \
  --command 'SELECT system_identifier FROM pg_control_system();'

podman exec todo-frontend \
  sha256sum /var/lib/todo-tls/ca.crt
```

## Run the migration

From the extracted operations package on the current primary:

```bash
ansible-playbook \
  --syntax-check \
  --inventory ansible/inventory-recovery.ini \
  ansible/migrate-application-to-kube.yml

ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/migrate-application-to-kube.yml \
  --extra-vars \
  '{"todo_confirm_application_kube_migration":"todo-standby"}'
```

Replace `todo-standby` with the exact inventory host name of the current
primary. The playbook refuses to proceed when the confirmation, host identity,
node address, writable database or rollback source files do not match.

The role copies the installed `.container` files to:

```text
~/.config/todo/quadlet-application-backup/
```

before stopping any application service. It then installs the shared workload
YAML and generated runtime ConfigMaps below:

```text
~/.config/containers/systemd/todo-kube-runtime/
```

Expected service names remain:

```text
todo-backend.service
todo-keycloak.service
todo-frontend.service
```

Podman container names change because Kube combines pod and container names:

```text
todo-backend-backend
todo-keycloak-keycloak
todo-frontend-nginx
```

## Verify

```bash
systemctl --user is-active \
  todo-postgres.service \
  todo-backend.service \
  todo-keycloak.service \
  todo-frontend.service

systemctl --user show \
  todo-backend.service \
  --property=SourcePath

systemctl --user show \
  todo-keycloak.service \
  --property=SourcePath

systemctl --user show \
  todo-frontend.service \
  --property=SourcePath

podman pod ps \
  --filter name=todo- \
  --format 'table {{.Name}}\t{{.Status}}'

curl --fail http://127.0.0.1:8080/ready
echo
```

The three application source paths must end in `.kube`; PostgreSQL must still
come from its accepted `.container` definition. Verify the public HTTPS routes,
stable Keycloak issuer and browser E2E flow before testing a reboot.

After migration, compare the unchanged identities with the recorded values:

```bash
podman exec todo-postgres \
  psql --username todo --dbname postgres --tuples-only --no-align \
  --command 'SELECT system_identifier FROM pg_control_system();'

podman exec todo-frontend-nginx \
  sha256sum /var/lib/todo-tls/ca.crt
```

## Roll back

If migration validation fails, use a separate shell on the current primary:

```bash
ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/rollback-application-to-container-quadlets.yml \
  --extra-vars \
  '{"todo_confirm_application_quadlet_rollback":"todo-standby"}'
```

The rollback requires all three preserved source files before stopping
anything. It removes only the Kube application runtime and its two derived
Kube-compatible secrets, restores the exact saved `.container` files and
starts the previous application chain. It does not remove or recreate any
volume, raw secret, PostgreSQL resource or backup file.

If both playbooks fail, leave PostgreSQL running, inspect the three application
units and use the preserved files manually. Do not modify the database or DR
state while diagnosing an application-tier migration.
