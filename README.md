# Todo and Notes demo

Two small reference applications for learning rootless Podman on Oracle Linux.
See [System architecture](docs/ARCHITECTURE.md) for the complete model and responsibility boundaries.

The repository demonstrates a complete lifecycle rather than only starting a
few containers: offline installation, least-privilege database access, HTTPS,
authentication, physical replication, controlled promotion, application
failover, backup, point-in-time recovery and restoration of redundancy for Todo,
Notes and the shared Keycloak database as one DR group. Notes adds independent
CRUD/storage and shared SSO.

## Architecture

```text
browser
   |
   | HTTPS :8443
   v
shared nginx proxy ----> shared Keycloak (todo realm) ---> keycloak-postgres
   |
   +--> todo-app ----------> todo-postgres
   |    frontend + backend
   |
   +--> notes-app ---------> notes-postgres
        frontend + backend

All three PostgreSQL databases replicate to the standby as one DR group.
```

- Plain HTML, CSS and JavaScript frontend
- FastAPI backend
- PostgreSQL 17.11
- Separate shared nginx proxy for TLS and routing; HTTP-only frontends
- Keycloak with Authorization Code and PKCE S256
- Rootless Podman Kube pods managed by `.kube` Quadlet and user systemd
- Python installer for single-host installation; Ansible for multi-host DR and verification
- OCI archives and checksums for offline delivery

Anyone can read Todos and Notes. A Keycloak login is required to create, update or delete
them. Per-user ownership is intentionally outside the demo.

## What you can learn

Deployment sources and entry points are collected in [deploy/](deploy/README.md).

Start with the current [Learning Guide](docs/LEARNING-GUIDE.md) and [Kube runtime guide](deploy/runtime/README.md).
Historical learning material is available in Git; see the
[history index](docs/history/README.md), not an alternate active runtime.

The shorter [concept coverage matrix](docs/WHAT-YOU-LEARN.md) states what the
demo implements, what it simplifies and which production concerns remain.

## Podman Kube runtime: start here

Clean install now deploys the final Podman Kube runtime directly. The former
per-container implementation remains recoverable from `quadlet-reference-v1`
and is no longer part of the active tree.

| Boundary | Files |
|---|---|
| Grouped application | `deploy/manifests/app.yaml.j2`; Python renders `todo-app.kube` |
| Notes app and database | `deploy/manifests/app.yaml.j2`, `postgres.yaml.j2`; `notes-app.kube`, `notes-postgres.kube` |
| Shared identity | `deploy/manifests/keycloak.yaml.j2`; `keycloak.kube` |
| Persistent databases | `deploy/manifests/postgres.yaml.j2`; `todo-postgres.kube`, `notes-postgres.kube`, `keycloak-postgres.kube` |
| Shared ingress | `deploy/manifests/shared-proxy.yaml.j2`; `shared-proxy.kube`, container `nginx` |
| Jinja2 manifest templates | [`deploy/manifests/`](deploy/manifests/) |
| Shared network | [`app-network.network`](deploy/quadlet/app-network.network) |

Start with the [Kube runtime guide](deploy/runtime/README.md). DR tools support the Todo, Notes and Keycloak databases as one group; retired PoCs and migration tooling remain in pre-retirement Git history.
Revision 688a0f6 passed full evidence-grade Oracle Linux acceptance of the
prior three-pod runtime; see the [run record](docs/ACCEPTANCE-688a0f6.md). The
historical four-pod shared-proxy runtime passed a lighter, process-level
two-agent acceptance on 9e54cfb; see [that run record](docs/ACCEPTANCE-9e54cfb.md).
The current seven-pod, three-database topology reached a REPAIRED FUNCTIONAL
PASS on 3fb897f; two standby-rebuild defects were worked around during the
run, so a clean pass is still pending. See [that run record](docs/ACCEPTANCE-3fb897f.md).

## Requirements

The deployed baseline requires:

- Oracle Linux 9 or a compatible Linux host
- rootless Podman with Quadlet support
- user systemd and lingering for boot-before-login operation
- Python 3.9+ and Jinja2; Ansible Core 2.14 or newer for DR
- Bash, `tar` and `sha256sum`
- configured `/etc/subuid` and `/etc/subgid` ranges

The full Oracle Linux acceptance drill uses two 4 GiB VMs, SELinux enforcing,
active `fapolicyd`, firewalld and a separate client machine. Tested versions and
site assumptions are recorded in [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md).

## Quick start

Install the portable installer on a connected development host:

```bash
python3 -m venv deploy/installer/.venv
deploy/installer/.venv/bin/python -m pip install -e deploy/installer
```

Deploy the complete single-host application:

```bash
deploy/installer/.venv/bin/python -m todo_installer install --mode server --project-root "$PWD"
```

Every password - PostgreSQL bootstrap, database-role and the Keycloak
administrator credential - is generated automatically on first run; none are
ever prompted for or printed. All values are stored as host-local Podman
secrets and are not written to the repository. Retrieve one later with
`podman secret inspect --showsecret <name>` (for example
`keycloak-admin-password`) when you actually need it. A normal repeat deployment preserves installed
definitions and credentials and does not restart unchanged workloads.
Use `--refresh-images` to rebuild/pull images. Direct development uses
`--mode dev` and `python -m todo_installer down`; the existing dev shell scripts
remain thin wrappers. See [installer usage](deploy/installer/README.md).

Both profiles expose <https://todo.test:8443> and <https://notes.test:8443>.
Map both names to the serving host (127.0.0.1 for direct development). nginx
creates one SAN certificate for both names from a persistent local demo CA; install only its public root on clients that should trust it. HTTP health checks remain available on
<http://127.0.0.1:8080>.

Inspect the running system:

```bash
systemctl --user is-active \
  todo-postgres.service \
  notes-postgres.service \
  notes-app.service \
  keycloak.service \
  todo-app.service \
  shared-proxy.service
podman ps
podman secret ls
curl --fail http://127.0.0.1:8080/ready
```

Quadlets live below `~/.config/containers/systemd/`. `todo-app.service` pulls in
`todo-postgres.service` and `keycloak.service`; `shared-proxy.service`
pulls in both apps and Keycloak. The app pod runs a migration init container before
backend and the HTTP-only frontend. Database-role provisioning is separate.
Generated units must not be enabled manually.

The proxy is an independent ingress boundary that could route to more services.
This demo uses Podman DNS routes with nginx DNS re-resolution and needs no dynamic proxy platform or
new orchestration. External clients reach proxy → frontend/backend/Keycloak →
PostgreSQL; TLS state belongs only to the proxy.

## Offline installation

Build the image bundle on a connected, target-compatible machine:

```bash
deploy/offline/build-bundle.sh
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
[deploy/offline/README.md](deploy/offline/README.md).

## Two-node operations

These runbooks protect Todo and its shared Keycloak schema. Notes replication,
backup, promotion and rebuild are a separate follow-up phase.

Build one source-only operations package:

```bash
deploy/scripts/build-operations-package.sh
```

It contains the two inventory templates, Ansible workflows, guarded DR/backup
tools and operational documentation. It contains no images, credentials,
site-specific inventory, SSH keys or database data.

For a complete exercise, follow the single sequence in
[Acceptance](docs/ACCEPTANCE.md). The [Ansible operation references](deploy/ansible/README.md)
explain each tool's scope and safety contracts. Use
[Acceptance troubleshooting](docs/ACCEPTANCE-TROUBLESHOOTING.md) for failed gates.

The original machine names remain stable after promotion; inventory groups
describe current roles. Promotion restores availability. Destructive re-seeding
of the old primary restores redundancy. Failback is a separate planned
operation.

For the exact build-from-zero acceptance procedure and pass criteria, use
[docs/ACCEPTANCE.md](docs/ACCEPTANCE.md).

## Security scope

This is a reference demo, not a production deployment baseline.

- SELinux remains enforcing; [docs/SELINUX.md](docs/SELINUX.md) separates labels,
  rootless UID mapping and ordinary permissions.
- `fapolicyd` remains active; [deploy/offline/FAPOLICYD.md](deploy/offline/FAPOLICYD.md)
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

Database-free migration startup retry tests live in `todo-backend/unit_tests/`
and `notes-backend/unit_tests/`.
Backend integration tests use an isolated database whose name must end in
`_test`:

```bash
podman exec todo-postgres createdb -U todo -O todo todo_test
python3 -m venv todo-backend/.venv
todo-backend/.venv/bin/python -m pip install -r todo-backend/requirements-test.txt
TODO_DB_PASSWORD=$(podman secret inspect --showsecret --format '{{.SecretData}}' todo-db-password)
export TEST_DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo_test user=todo password=$TODO_DB_PASSWORD"
todo-backend/.venv/bin/python -m pytest todo-backend/tests
unset TEST_DATABASE_URL TODO_DB_PASSWORD
```

Browser tests use `todo-backend/requirements-e2e.txt` and Playwright. The helper
creates or updates `testuser` without storing either password:

```bash
deploy/scripts/run-e2e.sh
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
| Run or hand off acceptance; change VM IPs (humans and agents: start here) | [Acceptance sequence](docs/ACCEPTANCE.md) |
| Check demonstrated versus simplified concepts | [What you learn](docs/WHAT-YOU-LEARN.md) |
| Operate deployment and recovery | [Ansible operations](deploy/ansible/README.md) |
| Understand SELinux and rootless ownership | [SELinux](docs/SELINUX.md) |
| Understand runtime credentials | [Secrets](docs/SECRETS.md) |
| Understand nginx and certificate trust | [TLS](docs/TLS.md) |
| Install without network access | [Offline bundle](deploy/offline/README.md) |
| Diagnose `fapolicyd` | [fapolicyd](deploy/offline/FAPOLICYD.md) |
| Read design history and live findings | [Development journal](docs/history/DEVELOPMENT-JOURNAL.md) |

## API

- `GET /health`
- `GET /ready`
- `GET /api/todos`
- `POST /api/todos` - authenticated
- `PUT /api/todos/{id}` - authenticated
- `DELETE /api/todos/{id}` - authenticated

## License

See [LICENSE](LICENSE).
