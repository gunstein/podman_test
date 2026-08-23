# Todo demo

A small, pedagogical Todo application for learning rootless Podman, Quadlet,
Ansible, offline installation, HTTPS and Keycloak.

## Architecture

- Plain HTML, CSS and JavaScript in the browser
- FastAPI backend
- PostgreSQL database
- Caddy serving static files, HTTPS and reverse proxy routes
- Keycloak authentication
- Rootless Podman containers managed by Quadlet and user systemd
- Ansible deployment, including an offline bundle

Anyone can read Todos. A Keycloak login is required to create, update or delete
them. All authenticated users share the same Todo list; per-user ownership is
intentionally outside this demo.

## Prerequisites

- Rootless Podman
- Python 3 with `venv`
- User systemd
- Bash

The offline target also needs `tar` and `sha256sum`.

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

Open the application at <https://localhost:8443>. Caddy uses its internal CA, so
an untrusted-certificate warning is expected until its local root certificate is
trusted. HTTP remains available at <http://127.0.0.1:8080>, but Keycloak login is
configured for the HTTPS address.

## Inspect the deployment

```bash
systemctl --user status todo-postgres.service
systemctl --user status todo-migrate.service
systemctl --user status todo-backend.service
systemctl --user status todo-keycloak.service
systemctl --user status todo-frontend.service

podman ps
podman network inspect todo-network
podman secret ls
```

Starting `todo-frontend.service` pulls in this dependency chain:

```text
PostgreSQL healthy -> migrations -> backend and Keycloak -> Caddy/frontend
```

Quadlet files are installed in `~/.config/containers/systemd/`. Podman's
systemd generator turns them into generated user services; generated services
are started, but not enabled with `systemctl enable`.

## Verify HTTP and HTTPS

```bash
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
curl --fail http://127.0.0.1:8080/api/todos

podman cp todo-frontend:/data/caddy/pki/authorities/local/root.crt \
  /tmp/todo-caddy-root.crt
curl --fail --cacert /tmp/todo-caddy-root.crt \
  https://localhost:8443/health
```

`/health` verifies that the backend process is alive. `/ready` additionally
checks its database connection.

The Caddy CA private key remains in the `todo-caddy-data` volume. Only the
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

The browser uses Authorization Code with PKCE S256. Access tokens remain in the
Keycloak JavaScript adapter's memory and are not stored in local storage.

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

This creates `dist/todo-offline-m11.tar.gz` containing:

- Backend, frontend, Keycloak and PostgreSQL OCI image archives
- Pinned Ansible wheels
- Quadlet and Ansible deployment files
- An installer and SHA-256 checksums

On the offline target:

```bash
tar -xzf todo-offline-m11.tar.gz
cd todo-offline-m11
./install.sh
```

The installer verifies every file before loading images and running the same
Ansible deployment without contacting a registry or Python package index. See
[offline/README.md](offline/README.md) for target assumptions.

## Uninstall

Remove services, containers, application images, network, secrets and installed
Quadlet files while preserving PostgreSQL data:

```bash
ansible/.venv/bin/ansible-playbook \
  --inventory ansible/inventory.ini \
  ansible/uninstall.yml
```

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
