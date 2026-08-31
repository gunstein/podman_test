# Todo demo

A small, pedagogical Todo application for learning rootless Podman, Quadlet,
Ansible, offline installation, HTTPS and Keycloak.

## Architecture

- Plain HTML, CSS and JavaScript in the browser
- FastAPI backend
- PostgreSQL database
- nginx serving static files, HTTPS and reverse proxy routes
- Keycloak authentication
- Rootless Podman containers managed by Quadlet and user systemd
- Ansible deployment, including an offline bundle

Anyone can read Todos. A Keycloak login is required to create, update or delete
them. All authenticated users share the same Todo list; per-user ownership is
intentionally outside this demo.

## Learning path

The complete stack is intentionally the result of small milestones:

```text
M1-M2   HTML/JavaScript, FastAPI, PostgreSQL and CRUD
M3-M5   Containerfiles, manual rootless Podman and nginx
M6-M8   Quadlet, Podman secrets and Ansible
M9-M11  Offline bundle, HTTPS and Keycloak
M12     Least privilege, image policy, tests and container hardening
M13     Ansible-provisioned PostgreSQL primary/standby
M13.5   Python tools for replication status and controlled promotion
M14     Full application disaster recovery
M15     Backup, WAL archive and point-in-time recovery
M16     Rebuild old primary as the new standby
```

See [PROJECT.md](PROJECT.md) for the decisions and verification performed at
each step. The [concept coverage matrix](docs/WHAT-YOU-LEARN.md) explains what
the demo implements versus deliberately simplifies. The
[nginx and TLS guide](docs/TLS.md) separates the local offline demo CA from
the recommended pre-provisioned PKI model. The
[Oracle Linux SELinux guide](docs/SELINUX.md) explains labels, rootless UID
mapping and how SELinux differs from fapolicyd. The
[secrets guide](docs/SECRETS.md) separates the demo bootstrap flow from the
recommended authoritative-source, runtime-delivery and rotation model.

## Security scope

This is a learning demo, not a production deployment baseline. Through M12,
all published ports are bound to `127.0.0.1`. M13 additionally publishes
PostgreSQL TCP 5432 on primary's replication interface; the host firewall must
restrict that port to standby. After promotion, M14 publishes HTTPS 8443 on
the promoted host's LAN address; its firewall must restrict access to the
intended clients. Before exposing the application to other hosts,
remove development-only ports, restrict Keycloak administration in the reverse
proxy, use trusted TLS, define backup and secret-rotation procedures, and
establish vulnerability scanning.

`E2E_IGNORE_HTTPS_ERRORS=true` is only for the local browser test using nginx's
development CA. It must not become an application or production setting.

## Prerequisites

- Rootless Podman
- Python 3 with `venv`
- User systemd
- Bash

The offline target also needs `tar` and `sha256sum`.

The complete stack has been tested with Podman 4.9.3, systemd 255 and
Python 3.12.3. Online Ansible development and syntax checks use maintained
Ansible Core 2.20.8; M13 CI also checks the Oracle Linux 9-compatible 2.14.18
baseline. These are tested baselines, not claims that every older version is
unsupported.

Keycloak's memory limit uses `PodmanArgs=--memory=1g` because the tested
Podman 4.9 Quadlet generator does not support the newer native `Memory=`
field. This small compatibility escape hatch can be replaced when the tested
Podman baseline is raised.

## Deploy the complete application

Create a dedicated Ansible virtual environment:

```bash
python3 -m venv ansible/.venv
ansible/.venv/bin/python -m pip install -r ansible/requirements.txt
```

Deploy from the project root:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/deploy.yml
```

The first run asks for:

- The PostgreSQL password
- An initial Keycloak administrator password

The values are stored as rootless Podman secrets and are never written to the
repository. Later runs reuse the existing secrets. A repeat deployment should
finish with `changed=0`.

That idempotent run deliberately reuses local images. To rebuild the application
against refreshed base images and pull the pinned PostgreSQL 17.11 image:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/deploy.yml \
  --extra-vars refresh_images=true
```

Use this explicit refresh after source changes and when applying image security
updates. Offline installation instead uses the exact images included in its
bundle.

Direct Python dependencies and base-image patch versions are pinned in Git.
Updating them is deliberate: change the reviewed pins, run the refresh
deployment, execute the tests and then build a new offline bundle.

Open the application at <https://localhost:8443>. The nginx entrypoint uses OpenSSL to create a local demo CA, so
an untrusted-certificate warning is expected until its local root certificate is
trusted. HTTP remains available at <http://127.0.0.1:8080>, but Keycloak login is
configured for the HTTPS address.

## Inspect the deployment

```bash
systemctl --user status todo-postgres.service
systemctl --user status todo-db-setup.service
systemctl --user status todo-migrate.service
systemctl --user status todo-db-grants.service
systemctl --user status todo-backend.service
systemctl --user status todo-keycloak.service
systemctl --user status todo-frontend.service

podman ps
podman network inspect todo-network
podman secret ls
```

The bootstrap database account is used only by PostgreSQL initialization and the
idempotent role-setup service. Runtime access is split:

- `todo_migrator` owns the Todo schema and runs migrations
- `todo_app` receives only Todo table CRUD and sequence usage
- `keycloak_app` owns only the Keycloak schema

The roles cannot inherit one another's privileges: `todo_app` cannot read
migration or Keycloak data, `todo_migrator` cannot read Keycloak data, and
`keycloak_app` cannot read Todo data. Clean-install CI verifies this matrix.

Their independent passwords are generated by Ansible and stored as rootless
Podman secrets. Podman's default `file` secret driver is protected local file
storage; it is not an external vault or hardware-backed secret store. Podman's
`pass` driver can be selected separately for GPG-encrypted local storage. See
[Secrets in the Todo demo](docs/SECRETS.md) before treating either local driver
as a source of truth or designing credential rotation.

Starting `todo-frontend.service` pulls in this dependency chain:

```text
PostgreSQL healthy
  -> least-privilege database roles
  -> migrations complete
  -> final runtime grants
  -> backend and Keycloak start
  -> nginx/frontend starts
```

Systemd ordering means the long-running processes have started, not necessarily
that they are ready. Ansible subsequently verifies backend health and readiness
and Keycloak discovery.

Quadlet files are installed in `~/.config/containers/systemd/`. Podman's
systemd generator turns them into generated user services. The frontend
Quadlet's `[Install] WantedBy=default.target` makes it the single automatic
entrypoint; its dependencies pull in the rest of the stack. Generated services
must not be enabled manually with `systemctl enable`.

With a normal user session, the stack starts when that user's systemd manager
starts after login. For an always-on server that must start before the service
user logs in, an administrator must explicitly enable lingering:

```bash
sudo loginctl enable-linger <service-user>
```

The Ansible playbook intentionally does not make this host-level administrative
decision.

## PostgreSQL standby and controlled promotion (M13-M13.5)

M13 adds one asynchronous PostgreSQL standby on a second host. The primary is
the Ansible controller, while the standby remains locally operable if primary
is lost. M13.5 adds a small standard-library Python tool that runs locally on
standby and never depends on primary during a disaster. Start with:

- [Primary/standby preparation](ansible/M13.md)
- [Replication bootstrap and verification](ansible/M13-BOOTSTRAP.md)
- [Controlled promotion runbook](ansible/M13.5-PROMOTION.md)

Build the small source-only transfer package with:

```bash
scripts/build-m13-test-package.sh
```

This creates `dist/todo-m13-test.tar.gz` and a matching
`dist/todo-m13-test.tar.gz.sha256`. The archive contains the M13/M13.5 Ansible
files, DR tool and shared PostgreSQL Quadlets; it contains no inventory, SSH
key, secret, image or database data. Verify the checksum against a value
received through a trusted channel before trusting extracted files. Replication
is availability protection, not backup.

The demo targets database RTO within 15 minutes and an operational RPO of at
most 30 seconds while asynchronous replication is healthy. That RPO is not a
guarantee: standby cannot detect commits that were never transmitted by a lost
primary.

Install or update the local tool before an incident:

On an active `fapolicyd` controller, first trust the verified extracted
`scripts/todo_dr.py` as described in the promotion runbook. Then run:

```bash
ansible-playbook \
  --inventory ansible/inventory-m13.ini \
  ansible/install-dr-m13.yml
```

A normal, non-mutating check is then run on standby:

```bash
python3 ~/.config/todo/todo_dr.py status
```

Actual promotion is irreversible for the existing replication topology. Follow
the runbook and fence primary before running `preflight` or `promote`. M13.5
promotes only PostgreSQL; the remaining application failover belongs to M14.
The controlled Oracle Linux 9.8 drill completed the promotion command in about
two seconds and verified a writable transaction. This is not a measurement of
the complete application RTO.

## Application failover (M14)

M14 loads the already staged M12 application images on the promoted standby and
starts dedicated backend, Keycloak and nginx Quadlets without running database
bootstrap, migrations or grants. The stable LAN identity is
`https://todo.test:8443`. Follow the [M14 failover runbook](ansible/M14-FAILOVER.md).
The lab copies the newly generated local OpenSSL public root to the client after
promotion to expose the TLS trust chain. A real DR design should normally
pre-stage client trust and server certificates, use an organization/public CA,
or terminate TLS at a stable redundant endpoint so certificate work does not
consume failover time. The runbook compares these alternatives and their
private-key, dependency and operational tradeoffs.

Build its source-only package and checksum with:

```bash
scripts/build-m14-test-package.sh
```

The package contains no secrets, inventory, images or database data. The M12
offline image bundle must already be present on standby. The earlier clean drill used Caddy. On 2026-08-31, the promoted Oracle Linux 9.8
host was migrated to the verified nginx image. HTTPS, the stable Keycloak
issuer, authenticated browser writes, an idempotent M14 rerun, reboot recovery,
M15 archive health and M16 streaming replication all passed. A future from-zero
drill will therefore start with nginx; the complete destructive M12-M16 sequence
did not need to be repeated for this proxy-only migration.

## Backup and point-in-time recovery (M15)

M15 uses a separate Podman volume for physical base backups and continuously
archived WAL, then restores to an isolated disposable container without touching
the live database. See the [M15 backup and PITR runbook](ansible/M15-BACKUP-PITR.md).
The local backup volume demonstrates recovery from logical damage; it is not
protection against loss of the VM or physical host. A live Oracle Linux 9.8 drill
verified the base backup manifest, named-point recovery with before/after data,
disposable cleanup, persistence across reboot and an idempotent Ansible rerun.

Build the source-only package with:

```bash
scripts/build-m15-test-package.sh
```

## Restore redundancy after failover (M16)

M16 keeps the promoted host as current primary and destructively re-seeds the
fenced old primary as a database-only standby. It requires stopped services, an
absent new replication slot and exact fencing/reseed confirmations before old
data can be removed. Follow the
[M16 restore-redundancy runbook](ansible/M16-RESTORE-REDUNDANCY.md).

Build its source-only package and checksum with:

```bash
scripts/build-m16-test-package.sh
```

The live Oracle Linux 9.8 two-VM drill passed: the old primary was quarantined
and re-seeded, an authenticated application write reached the rebuilt standby
with zero lag, and replication, application readiness and WAL archiving survived
reboot of both final database nodes. Planned failback remains deliberately
separate from restoring redundancy.

## Verify HTTP and HTTPS

```bash
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
curl --fail http://127.0.0.1:8080/api/todos

podman cp todo-frontend:/var/lib/todo-tls/ca.crt \
  /tmp/todo-nginx-root.crt
curl --fail --cacert /tmp/todo-nginx-root.crt \
  https://localhost:8443/health
```

`/health` verifies that the backend process is alive. `/ready` additionally
checks its database connection.

The local OpenSSL CA private key remains in the `todo-nginx-data` volume. Only the
public root certificate should be copied out. Never commit CA keys or secrets.

## Automated backend tests

Start PostgreSQL and create an isolated test database once:

```bash
podman exec todo-postgres createdb -U todo -O todo todo_test
```

Create the backend environment and run the tests:

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements-test.txt

read -rsp "Database password: " TODO_DB_PASSWORD
echo
export TODO_DB_PASSWORD
export TEST_DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo_test user=todo password=$TODO_DB_PASSWORD"
python -m pytest backend/tests
unset TEST_DATABASE_URL TODO_DB_PASSWORD
```

The test suite refuses database names that do not end in `_test`. It migrates
the test database, clears Todos between tests and rolls the schema back when
finished.

## Continuous integration

`.github/workflows/clean-install.yml` installs the pinned test dependencies
against an empty PostgreSQL 17.11 database. It exercises database bootstrap,
migrations, the explicit database-role privilege matrix and all backend tests.
A matrix syntax-checks all M12-M16 playbooks with Ansible Core 2.14.18 on
Python 3.9 and the maintained 2.20.8 development version on Python 3.12. A
separate job runs pinned ansible-lint 26.8.0 against all Ansible content.
Another pinned job runs Ruff against project Python and ShellCheck against the
project-owned shell scripts; local virtual environments are excluded.
The same matrix runs dependency-free unit tests for M13.5 fencing and promotion
plus M15 backup and disposable-restore safety boundaries.
Third-party actions are pinned to immutable commit SHAs and the workflow token
has read-only repository access.

CI deliberately does not emulate user systemd, rootless Quadlet, Keycloak,
nginx or browser E2E. Those integration layers are tested locally with the
Ansible deployment and `scripts/run-e2e.sh`.

## End-to-end browser tests

Install the test-only browser dependencies once:

```bash
source backend/.venv/bin/activate
python -m pip install -r backend/requirements-e2e.txt
python -m playwright install chromium
```

Run the helper:

```bash
scripts/run-e2e.sh
```

The helper asks for a test-user password and the current Keycloak administrator
password, then creates or updates a complete Keycloak user named `testuser`.
It runs both the public-read test and the authenticated CRUD test. Passwords
exist only in process memory or environment variables during the run; they are
not stored in Git or a project file.

The test user remains in Keycloak for repeatable local testing, and the helper
sets its password on each run. The uniquely named Todo created by the successful
test is deleted before the test finishes.

## Keycloak administration

Open <https://localhost:8443/auth/admin/> and log in as `admin` with the
administrator password chosen during the first deployment. The application realm
is `todo`.

This account is Keycloak's temporary bootstrap administrator. The localhost demo
intentionally retains it for repeatable administration and E2E user setup. A
real deployment should create a permanent, narrowly authorized administrator
and then remove the bootstrap account.

The browser uses Authorization Code with PKCE S256. Access tokens remain in the
Keycloak JavaScript adapter's memory and are not stored in local storage.

The administration console is safe only within this localhost scope. If Keycloak
is exposed to a network, its administration UI and Admin REST API must be
restricted at the reverse proxy; a separate hostname alone is not access
control.

The imported frontend redirect URI is the exact
`https://localhost:8443/`. Keycloak skips realm import when that realm already
exists, so later edits to `keycloak/todo-realm.json` require an explicit update
through the admin console/API or a deliberate clean install.

## Local backend development

For backend-only development, PostgreSQL can remain in Podman while Uvicorn runs
on the host:

```bash
source backend/.venv/bin/activate
read -rsp "Database password: " TODO_DB_PASSWORD
echo
export TODO_DB_PASSWORD
export DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD"
python -m backend.migrate up
uvicorn backend.main:app --reload
```

This intentionally uses the PostgreSQL bootstrap account to keep the local-only
development recipe short. The deployed backend uses the restricted `todo_app`
role instead.

Open <http://127.0.0.1:8000/docs>. Public endpoints work without OIDC settings.
Testing authenticated writes is simplest through the complete deployment.

Migration commands:

```bash
python -m backend.migrate status
python -m backend.migrate up
python -m backend.migrate down
```

`down` rolls back one migration and can delete data. Review the corresponding
`.down.sql` file before using it.

## Offline bundle

On a connected machine compatible with the target:

```bash
offline/build-bundle.sh
```

This creates `dist/todo-offline-m12.tar.gz` and
`dist/todo-offline-m12.tar.gz.sha256`. The archive contains:

- Backend, frontend, Keycloak and PostgreSQL OCI image archives
- Quadlet and Ansible deployment files
- A target preflight check, installer and SHA-256 checksums

On the offline target, copy both files through the trusted transfer path, then
verify before extraction:

```bash
sha256sum -c todo-offline-m12.tar.gz.sha256
tar -xzf todo-offline-m12.tar.gz
cd todo-offline-m12
sh ./preflight.sh
sh ./install.sh
```

The external checksum verifies the archive before any extracted script runs.
The internal `SHA256SUMS` then verifies every bundled artifact. SHA-256 alone
provides integrity only when the checksum arrived through a trusted channel; a
real release should sign the archive or checksum manifest.

The installer verifies every file before loading images and running the same
Ansible deployment without contacting a registry or Python package index. See
[offline/README.md](offline/README.md) for target assumptions.

The target must already have rootless Podman with Quadlet, functional user
systemd, ansible-core 2.14 or newer, `tar` and `sha256sum`. Rootless user
namespaces must be configured, normally through `/etc/subuid` and
`/etc/subgid`. The included preflight also checks the required localhost
ports and reports available memory and disk space.

The explicit `sh` invocation also works on Ubuntu and is required for the first
run on hosts where `fapolicyd` blocks direct execution of newly extracted
scripts.

Oracle Linux 9 with active `fapolicyd` is supported explicitly. The installer
uses Oracle Linux's RPM-managed ansible-core, so no executable Python code from
the bundle needs custom trust entries. Both SELinux and `fapolicyd` remain
enabled.

Installation details are in [offline/README.md](offline/README.md).
Denial diagnostics, project-owned Python trust and trust-entry cleanup are in
[offline/FAPOLICYD.md](offline/FAPOLICYD.md).

SHA-256 checks detect modified files only when `SHA256SUMS` itself is trusted.
They provide integrity, not publisher authenticity. A real distribution process
should deliver and verify a separately signed manifest or bundle.

## M12 uninstall

This playbook is intentionally limited to the single-host M12 deployment. It
refuses to run when it detects M13-M16 replication, promotion or backup state;
those hosts require their milestone runbook and an explicit topology decision.

Remove services, containers, application images, network and installed Quadlet
files while preserving PostgreSQL data:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/uninstall.yml
```

The database-related Podman secrets are preserved with the volume because an
existing PostgreSQL or Keycloak database still requires its existing
credentials. They are deleted together with the database only when
`remove_data=true`.

Standard uninstall always removes `todo-nginx-data`. Reinstalling therefore
creates a new local OpenSSL demo CA; remove the previously trusted demo certificate and
trust the new public root certificate if you had installed it locally.

Permanently delete the PostgreSQL volume and all Todo and Keycloak data:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/uninstall.yml \
  --extra-vars remove_data=true
```

## API

- `GET /health` — public liveness
- `GET /ready` — public database readiness
- `GET /api/todos` — public
- `POST /api/todos` — requires login
- `PUT /api/todos/{todo_id}` — requires login
- `DELETE /api/todos/{todo_id}` — requires login

Milestone history and design decisions are documented in
[PROJECT.md](PROJECT.md).

## License

The project is licensed under the [MIT License](LICENSE). Vendored components
retain their own license notices; the Keycloak browser adapter is covered by
`frontend/vendor/LICENSE.txt`.
