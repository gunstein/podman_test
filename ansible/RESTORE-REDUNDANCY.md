# Restore database redundancy after failover


M16 restores a second database copy after M13.5 promotion and M14 application
failover. It does not move service back to the machine that was originally
primary. The promoted host remains primary; the old primary is destroyed and
re-seeded as a new read-only standby.

```text
todo-standby (current primary, app, backup)
        │
        │ pg_basebackup + async WAL streaming
        ▼
todo-primary (rebuilt standby, database only)
```

Promotion restored availability. This procedure restores redundancy.

## Safety boundary

The old primary contains a divergent writable database and must never be allowed
to serve clients or replicate as primary. Keep it fenced at the Proxmox layer.
Powering it on is safe only with client/database traffic still blocked and for
the purpose of stopping its services and rebuilding it.

The rebuild playbook permanently deletes `todo-postgres-data` on the old primary.
It requires both exact values:

```text
todo-primary is fenced
todo-primary
```

A Proxmox snapshot may be retained for investigation, but it must not later be
booted onto the production network as a writable database.

## Quarantine the old primary

Keep the VM fenced. Boot it from the Proxmox console with normal client and
database traffic blocked. Log in as the rootless Todo service user and stop all
Todo services before allowing management SSH:

```bash
systemctl --user stop \
  todo-frontend.service \
  todo-backend.service \
  todo-keycloak.service \
  todo-postgres.service

systemctl --user is-active \
  todo-frontend.service \
  todo-backend.service \
  todo-keycloak.service \
  todo-postgres.service || true
podman ps
```

All four services must be inactive and no `todo-postgres` container may run.
Fencing is an infrastructure property; an unreachable TCP port alone is not
proof that the VM cannot serve other clients.

## Stage the operations package

Build and verify on the trusted source host:

```bash
scripts/build-operations-package.sh
(
  cd dist
  sha256sum -c todo-operations.tar.gz.sha256
)
```

Transfer both files to the current primary, verify before extraction and copy
the inventory example:

```bash
cd "$HOME"
sha256sum -c todo-operations.tar.gz.sha256
mkdir -p todo-operations
tar -xzf todo-operations.tar.gz \
  --strip-components=1 \
  --directory todo-operations
cd todo-operations
cp ansible/inventory-recovery.example.ini ansible/inventory-recovery.ini
```

Edit the two addresses. `todo_current_primary` is the promoted host and
`todo_rebuild_standby` is the fenced old primary. The package-level
`ansible.cfg` enables pipelining consistently for local and SSH connections. On
Oracle Linux with active `fapolicyd`, this avoids executing transient Ansible
module files without turning a transport setting into inventory data.

## Restrict the new replication endpoint

Before the rebuild workflow publishes PostgreSQL on the current primary LAN address, allow only
the rebuilt standby through firewalld. Substitute the two addresses:

```bash
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family="ipv4" source address="192.168.0.110/32" destination address="192.168.0.109" port port="5432" protocol="tcp" accept'
sudo firewall-cmd --reload
```

Do not enable the general PostgreSQL service. Rootless port forwarding means
PostgreSQL sees the Podman subnet; firewalld enforces the real host boundary.
Replication remains SCRAM-authenticated but unencrypted on the trusted demo LAN.

## Read-only preflight

From the current primary:

```bash
ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/preflight-standby-rebuild.yml \
  --extra-vars '{"todo_confirm_old_primary_fenced":"todo-primary is fenced","todo_confirm_reseed":"todo-primary"}'
```

Preflight requires the promoted database to be writable, the replication role
and secrets to exist, both hosts to have the identical replication credential,
the new slot to be absent, the old data volume to exist, and every Todo
service/container on the rebuild host to be stopped. Secret values remain
under Ansible `no_log`.

## Destructive rebuild

Read the recap and confirmations again, then run:

```bash
ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/rebuild-standby.yml \
  --extra-vars '{"todo_confirm_old_primary_fenced":"todo-primary is fenced","todo_confirm_reseed":"todo-primary"}'
```

The playbook first preserves M15 archiving while adding a restricted LAN
replication endpoint to the current primary. Before deleting anything, the
rebuild host both reaches TCP 5432 and authenticates a physical replication
connection with `IDENTIFY_SYSTEM`. Only then does it remove the old volume,
create a physical slot, stream a new base backup and start PostgreSQL in
recovery.

It removes application-tier Quadlets from the rebuilt host. That host now runs
only PostgreSQL standby; nginx, Keycloak and the backend stay on current primary.

This is a one-shot workflow. If it fails after volume removal or slot creation,
do not rerun blindly. Inspect the slot, partial volume and `pg_basebackup` error,
then perform explicit cleanup before another attempt.

## Verify restored redundancy

```bash
ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/cluster-status.yml
```

Current primary must report an active `streaming|async` connection, a usable
physical slot, writable state, `archive_mode=on` and a successful WAL archive
that is newer than any historical archive failure. Rebuilt standby must report
`recovery=t`, `read_only=on` and non-empty receive/replay LSNs.

After the rebuild, copy `ansible/inventory-recovery.example.ini` to a
site-specific, ignored inventory and keep it as the steady-state role map. Machine names remain
`todo-primary` and `todo-standby`, but the `todo_current_primary` and
`todo_current_standby` groups state their current database roles. The initial-topology inventory remains an input to first bootstrap; the
recovery inventory becomes the single role-based source of truth after promotion.

Create a Todo through `https://todo.test:8443`, then query it on rebuilt standby.
Finally reboot the rebuilt standby, verify recovery/streaming again, reboot the
current primary and verify app readiness, archiving and replication again.

## Acceptance evidence

The canonical destructive re-seed, replicated application write and two-node reboot results are recorded in [../docs/LAB-ACCEPTANCE.md](../docs/LAB-ACCEPTANCE.md). Development history remains in [../PROJECT.md](../PROJECT.md).

## Failback is separate

The healthy end state may remain:

```text
todo-standby = primary
todo-primary = standby
```

Returning primary service to `todo-primary` would be a planned switchover with
its own fencing, catch-up, service-address and rollback procedure. This rebuild workflow does not
automatically perform that separate operation.
