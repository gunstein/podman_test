# Learning guide

This guide describes the accepted per-container Quadlet reference. For the
grouped Podman Kube candidate, start with
[`kube/runtime/README.md`](../kube/runtime/README.md).

This guide explains the finished system in dependency order. Each section says
what to understand, where to look and what to try on a running lab. Commands are
observational unless the section explicitly links to a runbook.

## 1. Architecture

**Understand**

- Browser traffic enters through one nginx endpoint.
- nginx routes API and identity paths while serving static files.
- PostgreSQL is the stateful dependency; replication and backup solve different
  failure modes.

**Look at**

- `frontend/nginx.conf`
- `frontend/todo-proxy-headers.conf`
- `backend/main.py`
- `docs/WHAT-YOU-LEARN.md`

**Try**

```bash
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
podman ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

## 2. Container images

**Understand**

- Containerfiles define immutable application content; runtime state belongs in
  volumes and secrets.
- Application images run as non-root users with pinned base-image versions.
- The nginx image includes OpenSSL only for the self-contained demo TLS mode.

**Look at**

- `backend/Containerfile`
- `frontend/Containerfile`
- `keycloak/Containerfile`
- `.containerignore`

**Try**

```bash
podman image inspect localhost/todo-frontend:m12 \
  --format 'User={{.Config.User}} Proxy={{index .Labels "io.todo.proxy"}}'
podman exec todo-frontend nginx -t
```

## 3. Rootless Podman

**Understand**

- Containers run in a user namespace without a root-owned Podman daemon.
- `/etc/subuid` and `/etc/subgid` provide subordinate identity ranges.
- Root inside a rootless container is not host root.

**Look at**

- `offline/preflight.sh`
- `quadlet/`
- `docs/SELINUX.md`

**Try**

```bash
podman info --format 'Rootless={{.Host.Security.Rootless}} GraphRoot={{.Store.GraphRoot}}'
podman unshare cat /proc/self/uid_map
podman network inspect todo-network
```

## 4. Persistent storage

**Understand**

- PostgreSQL data, nginx CA state and backups have different lifecycles.
- `:U` adjusts ownership for a user namespace; `:Z` and `:z` adjust SELinux
  labels. They solve different problems.
- A named volume is persistent but is not automatically an off-host backup.

**Look at**

- `quadlet/todo-postgres-data.volume`
- `quadlet/todo-nginx-data.volume`
- `docs/SELINUX.md`
- `ansible/BACKUP-PITR.md`

**Try**

```bash
podman volume ls
podman volume inspect todo-postgres-data
podman unshare ls -ld "$(podman volume inspect todo-postgres-data --format '{{.Mountpoint}}')"
```

## 5. Quadlet and systemd

**Understand**

- Quadlet is the declarative bridge between Podman and user systemd.
- Generated services inherit systemd ordering, restart and boot behavior.
- The frontend is the single default-target entrypoint; dependencies pull in
  database preparation and long-running services.
- Lingering is a host-administrator decision for boot-before-login behavior.

**Look at**

- `quadlet/todo-frontend.container`
- `quadlet/todo-postgres.container`
- `quadlet/todo-migrate.container`
- `ansible/deploy.yml`

**Try**

```bash
systemctl --user list-dependencies todo-frontend.service
systemctl --user cat todo-postgres.service
loginctl show-user "$USER" -p Linger
```

## 6. Secrets

**Understand**

- Podman secrets are host-local runtime objects, not a centralized vault.
- The backend prefers file delivery; Keycloak uses environment delivery where
  the upstream application expects it.
- Standby provisioning transfers values through Ansible memory and SSH with
  value-bearing tasks suppressed by `no_log`.

**Look at**

- `docs/SECRETS.md`
- `ansible/tasks/sync_secret.yml`
- `quadlet/todo-backend.container`
- `quadlet/todo-keycloak.container`

**Try**

```bash
podman secret ls
podman inspect todo-backend --format '{{json .Config.Env}}'
podman exec todo-backend ls -l /run/secrets
```

Do not print secret values during an ordinary inspection.

## 7. nginx and TLS

**Understand**

- nginx owns the external HTTP contract but is not a certificate authority.
- The demo entrypoint creates a persistent local CA and leaf certificate.
- Client root trust and server private-key custody are opposite sides of TLS.
- A moderate deployment should pre-provision trust and per-node keys from a
  managed organization CA.

**Look at**

- `frontend/nginx.conf`
- `frontend/todo-nginx-entrypoint.sh`
- `docs/TLS.md`

**Try**

```bash
podman exec todo-frontend openssl x509 \
  -in /var/lib/todo-tls/server.crt -noout -subject -issuer -ext subjectAltName
podman exec todo-frontend stat -c '%a %n' \
  /var/lib/todo-tls/ca.key /var/lib/todo-tls/server.key
```

## 8. Ansible ownership

**Understand**

- Ansible installs and verifies desired state; it does not remain resident.
- systemd owns the runtime after deployment.
- Project-level pipelining avoids transient module files rejected by hardened
  `fapolicyd` configurations.

**Look at**

- `ansible.cfg`
- `ansible/deploy.yml`
- `ansible/README.md`
- `ansible/roles/`

**Try**

```bash
ansible-playbook --syntax-check \
  --inventory ansible/inventory.ini ansible/deploy.yml
ansible-inventory --inventory ansible/inventory-initial.ini --graph
```

## 9. Oracle Linux security boundaries

**Understand**

- Unix ownership, user-namespace mapping, SELinux, `fapolicyd` and firewalld are
  independent enforcement layers.
- An SELinux denial appears as an AVC; an `fapolicyd` denial appears through
  FANOTIFY/audit evidence.
- Disabling either control is not the supported fix.

**Look at**

- `docs/SELINUX.md`
- `offline/FAPOLICYD.md`
- `offline/preflight.sh`

**Try**

```bash
getenforce
systemctl is-active fapolicyd firewalld
sudo ausearch -m AVC -ts recent
sudo journalctl -u fapolicyd --since '15 minutes ago'
```

## 10. Offline installation

**Understand**

- OCI archives remove registry dependency at the target.
- Verify the archive checksum before extraction and the internal manifest after.
- `VERSION` connects an offline artifact to its source revision.
- RPM-managed host tools are preferred on `fapolicyd` systems.

**Look at**

- `offline/build-bundle.sh`
- `offline/preflight.sh`
- `offline/install.sh`
- `offline/README.md`

**Try**

```bash
sha256sum -c dist/todo-offline-m12.tar.gz.sha256
tar -xOf dist/todo-offline-m12.tar.gz todo-offline-m12/VERSION
```

## 11. PostgreSQL replication

**Understand**

- The standby is initialized with `pg_basebackup` and follows asynchronously.
- A physical slot retains WAL but can create capacity pressure or be invalidated.
- Replication improves availability; it reproduces logical mistakes and is not
  a backup.

**Look at**

- `ansible/roles/postgres_primary/`
- `ansible/roles/postgres_standby/`
- `ansible/replication-status.yml`
- `ansible/STANDBY-BOOTSTRAP.md`

**Try**

```bash
ansible-playbook --inventory ansible/inventory-initial.ini \
  ansible/replication-status.yml
```

## 12. Disaster recovery

**Understand**

- Infrastructure fencing must precede promotion to avoid split-brain.
- The DR tool checks health, endpoint reachability and apply lag, then requires
  exact human confirmations.
- Asynchronous RPO cannot guarantee receipt of commits never sent to standby.

**Look at**

- `scripts/todo_dr.py`
- `ansible/PROMOTION.md`
- `tests/test_todo_dr.py`

**Try**

```bash
python3 /opt/todo/bin/todo_dr.py status
```

Do not run `promote` outside the controlled runbook.

## 13. Application failover

**Understand**

- Database promotion and application failover are deliberately separate.
- Promoted deployment requires a writable database and existing runtime secrets;
  it does not rerun role bootstrap or grants; schema migration remains the grouped
  app init-container responsibility.
- `todo.test` keeps issuer and redirect identity stable while its address moves.

**Look at**

- `ansible/deploy-promoted-application.yml`
- `ansible/roles/promoted_application/`
- `ansible/APPLICATION-FAILOVER.md`

**Try**

```bash
curl --fail https://todo.test:8443/ready
curl --fail https://todo.test:8443/auth/realms/todo/.well-known/openid-configuration
```

## 14. Backup and PITR

**Understand**

- PITR needs a verified base backup plus an unbroken WAL sequence.
- A named restore point provides a precise learning boundary.
- Restore targets fixed, network-isolated disposable resources, never live data.
- `archive_timeout=1h` avoids the extreme low-traffic growth observed with a
  forced switch every minute.

**Look at**

- `scripts/todo_backup.py`
- `ansible/configure-backup.yml`
- `ansible/BACKUP-PITR.md`

**Try**

```bash
python3 /opt/todo/bin/todo_backup.py status
```

## 15. Restore redundancy

**Understand**

- Promotion restores availability but leaves only one current database copy.
- The divergent old primary is destroyed and re-seeded as a new standby.
- Secret equality, TCP reachability and authenticated `IDENTIFY_SYSTEM` all
  precede volume deletion.
- Returning service to the originally named primary is a separate switchover.

**Look at**

- `ansible/preflight-standby-rebuild.yml`
- `ansible/roles/postgres_reseed_standby/`
- `ansible/RESTORE-REDUNDANCY.md`
- `tests/test_ansible_safety.py`

**Try**

```bash
ansible-playbook --inventory ansible/inventory-recovery.ini \
  ansible/cluster-status.yml
```

For the destructive lifecycle in the correct order, follow
[LAB-ACCEPTANCE.md](LAB-ACCEPTANCE.md), not isolated commands from this guide.
