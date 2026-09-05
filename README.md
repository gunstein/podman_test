# Todo demo

A small reference application for learning how a stateful service can be built,
delivered and recovered with rootless Podman on Oracle Linux.

The repository demonstrates a complete lifecycle rather than only starting a
few containers: offline installation, least-privilege database access, HTTPS,
authentication, physical replication, controlled promotion, application
failover, backup, point-in-time recovery and restoration of redundancy.

## Architecture

```text
browser
   |
   | HTTPS :8443
   v
nginx --------> Keycloak
   |
   v
FastAPI backend
   |
   v
PostgreSQL primary =====async WAL=====> PostgreSQL standby
        |
        +----base backup + WAL archive----> backup volume
```

- Plain HTML, CSS and JavaScript frontend
- FastAPI backend
- PostgreSQL 17.11
- nginx for static content, TLS and reverse proxy routes
- Keycloak with Authorization Code and PKCE S256
- Rootless Podman Kube pods managed by `.kube` Quadlet and user systemd
- Ansible for installation, configuration and verification
- OCI archives and checksums for offline delivery

Anyone can read Todos. A Keycloak login is required to create, update or delete
them. Per-user ownership is intentionally outside the demo.

## What you can learn

Start with the current [Learning Guide](docs/LEARNING-GUIDE.md) and [Kube runtime guide](kube/runtime/README.md).
The [legacy reference learning guide](docs/legacy/LEARNING-GUIDE.md) preserves
the accepted per-container model for historical comparison.

The shorter [concept coverage matrix](docs/WHAT-YOU-LEARN.md) states what the
demo implements, what it simplifies and which production concerns remain.

## Podman Kube runtime: start here

Clean install now deploys the final Podman Kube runtime directly. The former
per-container implementation remains recoverable from `quadlet-reference-v1`
and is retained only as transition/rollback evidence.

| Boundary | Files |
|---|---|
| Grouped application | [`app.yaml`](kube/runtime/app.yaml), [`todo-app.kube`](kube/runtime/todo-app.kube) |
| Shared identity | [`keycloak.yaml`](kube/runtime/keycloak.yaml), [`todo-keycloak.kube`](kube/runtime/todo-keycloak.kube) |
| Persistent database | [`postgres.yaml`](kube/runtime/postgres.yaml), [`todo-postgres.kube`](kube/runtime/todo-postgres.kube) |
| Helm templates and values | [`helm/todo/`](helm/todo/) |
| Shared network | [`todo.network`](quadlet/todo.network) |

Start with the [Kube runtime guide](kube/runtime/README.md). PoCs, migrations,
DR automation and historical results are evidence and operations around this
core. The final runtime still requires the Oracle Linux acceptance gates.

## Requirements

The deployed baseline requires:

- Oracle Linux 9 or a compatible Linux host
- rootless Podman with Quadlet support
- user systemd and lingering for boot-before-login operation
- Ansible Core 2.14 or newer
- Python 3, Bash, `tar` and `sha256sum`
- configured `/etc/subuid` and `/etc/subgid` ranges

The full Oracle Linux acceptance drill uses two 4 GiB VMs, SELinux enforcing,
active `fapolicyd`, firewalld and a separate client machine. Tested versions and
site assumptions are recorded in [docs/LAB-ACCEPTANCE.md](docs/LAB-ACCEPTANCE.md).

## Quick start

Create a dedicated Ansible environment on a connected development host:

```bash
python3 -m venv ansible/.venv
ansible/.venv/bin/python -m pip install -r ansible/requirements.txt
```

Deploy the complete single-host application:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/deploy.yml
```

The first run asks for the PostgreSQL bootstrap password and a temporary
Keycloak administrator password. Other database-role passwords are generated
independently. All values are stored as host-local Podman secrets and are not
written to the repository. A normal repeat deployment should report
`changed=0`.

Open <https://localhost:8443>. nginx creates a persistent local demo CA and a
certificate for `localhost`; install only its public root on clients that should
trust it. HTTP health checks remain available on
<http://127.0.0.1:8080>.

Inspect the running system:

```bash
systemctl --user is-active \
  todo-postgres.service \
  todo-keycloak.service \
  todo-app.service
podman ps
podman secret ls
curl --fail http://127.0.0.1:8080/ready
```

Quadlets live below `~/.config/containers/systemd/`. `todo-app.service` pulls in
`todo-postgres.service` and `todo-keycloak.service`; the app pod runs a migration
init container before backend and nginx. Database-role provisioning is separate.
Generated units must not be enabled manually.

## Offline installation

Build the image bundle on a connected, target-compatible machine:

```bash
offline/build-bundle.sh
```

Transfer both generated files through a trusted path. On the target:

```bash
sha256sum -c todo-offline-m12.tar.gz.sha256
tar -xzf todo-offline-m12.tar.gz
cd todo-offline-m12
sh ./preflight.sh
sh ./install.sh
```

The external checksum is verified before extracted code runs. The internal
manifest verifies every bundled file, and `VERSION` records the source Git
revision plus clean/dirty build state. SHA-256 provides authenticity only when
the checksum itself came through a trusted channel. See
[offline/README.md](offline/README.md).

## Two-node operations

Build one source-only operations package:

```bash
scripts/build-operations-package.sh
```

It contains the two inventory templates, Ansible workflows, guarded DR/backup
tools and operational documentation. It contains no images, credentials,
site-specific inventory, SSH keys or database data.

Use the runbooks in this order for a complete exercise:

1. [Initial topology](ansible/STANDBY-ARCHITECTURE.md)
2. [Standby bootstrap](ansible/STANDBY-BOOTSTRAP.md)
3. [Controlled promotion](ansible/PROMOTION.md)
4. [Application failover](ansible/APPLICATION-FAILOVER.md)
5. [Backup and PITR](ansible/BACKUP-PITR.md)
6. [Restore redundancy](ansible/RESTORE-REDUNDANCY.md)

The original machine names remain stable after promotion; inventory groups
describe current roles. Promotion restores availability. Destructive re-seeding
of the old primary restores redundancy. Failback is a separate planned
operation.

For the exact build-from-zero acceptance procedure and pass criteria, use
[docs/LAB-ACCEPTANCE.md](docs/LAB-ACCEPTANCE.md).

## Security scope

This is a reference demo, not a production deployment baseline.

- SELinux remains enforcing; [docs/SELINUX.md](docs/SELINUX.md) separates labels,
  rootless UID mapping and ordinary permissions.
- `fapolicyd` remains active; [offline/FAPOLICYD.md](offline/FAPOLICYD.md)
  documents exact-file trust and the RPM/DNF scaling direction.
- [docs/SECRETS.md](docs/SECRETS.md) explains Podman-secret bootstrap,
  synchronization, runtime delivery, rotation and the single-node-loss boundary.
- [docs/TLS.md](docs/TLS.md) separates the local OpenSSL demo CA from a
  production PKI with pre-provisioned trust and per-node private keys.
- PostgreSQL replication is SCRAM-authenticated and firewalled but is not
  TLS-enforced on the trusted lab LAN.
- The backup volume demonstrates PITR but remains on the same VM; production
  requires off-host copies, retention, encryption, monitoring and restore tests.
- Simultaneous loss of both database nodes is outside scope. At least one node
  must survive with the required Podman secrets.

Never commit passwords, private keys, local inventories or generated bundles.

## Tests

Database-free migration startup retry tests live in `backend/unit_tests/`.
Backend integration tests use an isolated database whose name must end in
`_test`:

```bash
podman exec todo-postgres createdb -U todo -O todo todo_test
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-test.txt
read -rsp "Database password: " TODO_DB_PASSWORD
echo
export TEST_DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo_test user=todo password=$TODO_DB_PASSWORD"
backend/.venv/bin/python -m pytest backend/tests
unset TEST_DATABASE_URL TODO_DB_PASSWORD
```

Browser tests use `backend/requirements-e2e.txt` and Playwright. The helper
creates or updates `testuser` without storing either password:

```bash
scripts/run-e2e.sh
```

CI runs backend tests, Python and shell lint, nginx runtime smoke tests, Ansible
lint, safety regressions and syntax checks with both the Oracle Linux-compatible
Ansible 2.14.18 baseline and the maintained development version. Rootless
systemd, SELinux, `fapolicyd`, Keycloak and destructive DR are verified by the
manual lab acceptance test.

## Documentation map

| Need | Document |
|---|---|
| Understand the system architecture | [Architecture](docs/ARCHITECTURE.md) |
| Recover old primary without VM console | [Proxmox quarantine preparation](docs/PROXMOX-QUARANTINE.md) |
| Learn the system in dependency order | [Learning guide](docs/LEARNING-GUIDE.md) |
| Run or hand off acceptance; change VM IPs (humans and agents: start here) | [Acceptance checklist, commands and failure recovery](docs/MANUAL-DR-QUICKSTART.md) |
| Check demonstrated versus simplified concepts | [What you learn](docs/WHAT-YOU-LEARN.md) |
| Operate deployment and recovery | [Ansible operations](ansible/README.md) |
| Understand SELinux and rootless ownership | [SELinux](docs/SELINUX.md) |
| Understand runtime credentials | [Secrets](docs/SECRETS.md) |
| Understand nginx and certificate trust | [TLS](docs/TLS.md) |
| Install without network access | [Offline bundle](offline/README.md) |
| Diagnose `fapolicyd` | [fapolicyd](offline/FAPOLICYD.md) |
| Read design history and live findings | [Development journal](PROJECT.md) |

## API

- `GET /health`
- `GET /ready`
- `GET /api/todos`
- `POST /api/todos` - authenticated
- `PUT /api/todos/{id}` - authenticated
- `DELETE /api/todos/{id}` - authenticated

## License

See [LICENSE](LICENSE).
