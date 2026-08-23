# Project status

## Goal

Learn rootless Podman by building and deploying a small Todo application.

The project should demonstrate Podman, Quadlet, Ansible and eventually offline installation in small, understandable steps.

## Current milestone

M11 — Add Keycloak authentication last.

## Completed

- `GET /health` returns `{"status": "ok"}`.
- PostgreSQL runs in a rootless Podman container with a named volume.
- The Todo API supports create, read, update and delete operations.
- The frontend uses plain HTML, CSS and JavaScript.
- The frontend can create, complete and delete Todos.
- FastAPI serves the frontend.
- The README contains local development instructions.
- PostgreSQL data was verified to survive a container stop and restart.
- Versioned SQL migrations support status, upgrade and single-step rollback.
- Migration upgrade and rollback were verified against a disposable test database.
- Nine automated backend tests cover health, readiness success and failure, frontend serving, CRUD, title validation, missing Todos, migration file pairs and idempotence.
- Tests were verified against an isolated, automatically removed PostgreSQL test container.
- Backend and frontend images build successfully and pass isolated smoke tests as UID 1000.
- PostgreSQL, backend and frontend run manually on the rootless `todo-network`.
- Backend reaches PostgreSQL through Podman DNS using the `todo-postgres` name.
- Database persistence was verified across a PostgreSQL container stop and start.
- Container logs, inspect data, routes and lifecycle were used to diagnose and repair a Netavark namespace issue.
- Liveness (`/health`) and database readiness (`/ready`) are separate endpoints.
- Caddy provides one browser-facing endpoint and routes frontend and API traffic by path.
- Full Todo CRUD was verified through Caddy.
- One Chromium E2E test verifies the complete browser Todo flow through Caddy.
- Rootless Quadlet definitions describe the network, persistent volume, PostgreSQL, migration, backend and frontend.
- Quadlet dependencies enforce PostgreSQL health, completed migrations, backend startup and frontend startup in that order.
- The Quadlet lifecycle and browser flow were verified through user systemd.
- PostgreSQL, migrations and the backend receive the database password from a file-mounted rootless Podman secret.
- The secret-based deployment passed health, readiness, API and Chromium E2E checks.
- A small localhost Ansible playbook builds the images, installs Quadlet files, creates the Podman secret when missing, starts the services and verifies health and readiness.
- A second Ansible run completed with `changed=0`, confirming idempotence.
- The Ansible-deployed application passed the Chromium E2E test.
- An offline tar.gz bundle contains OCI image archives, pinned Ansible wheels, deployment files, an installer and SHA-256 checksums.
- Offline installation was verified after removing all three local images and blocking registry and package-index access with invalid proxies.
- PostgreSQL data survived the offline reinstall.
- Caddy serves locally issued HTTPS on `https://localhost:8443` while retaining HTTP on port 8080 for development.
- Caddy's internal CA persists in a dedicated rootless Podman volume.
- TLS hostname, certificate chain, health, readiness and Chromium E2E behavior were verified.
- M1 through M10 are complete.
- Keycloak runs behind Caddy and imports a minimal `todo` realm with a public SPA client using PKCE S256.
- Todo reads remain public, while create, update and delete operations require a valid Keycloak access token.
- Keycloak uses the existing PostgreSQL service with a separate database schema created by migration 002.
- The initial Keycloak administrator password is held in a rootless Podman secret and no users or passwords are committed.
- Public-read and authenticated CRUD browser flows were verified through Keycloak.
- A helper provisions the complete `testuser` account and runs both E2E flows without storing test passwords.
- M11 is complete.

## Decisions

- Implement and understand the application before containerizing it.
- Use rootless Podman.
- Run containers manually before introducing Quadlet and Ansible.
- Prefer simple, pedagogical solutions over abstraction.
- Keep dependencies and Bash scripts minimal.
- Run PostgreSQL in a rootless Podman container during M2 instead of installing it on the host.
- Keep FastAPI running locally through M2; containerize it in M3.
- Add Keycloak last.
- Keep liveness and readiness checks separate.
- Use one Caddy-based frontend container to serve static files and proxy backend routes.
- Keep Playwright and Chromium as separate test-only dependencies.
- Mount the database password as a file from rootless Podman secret storage.
- Keep `DATABASE_URL` for local development and tests; containers use separate non-secret settings plus `DATABASE_PASSWORD_FILE`.
- Enable only the frontend unit; systemd starts the remaining application dependency chain.
- Start Ansible with one localhost playbook and only `ansible-core`; add remote deployment structure only when it is needed.
- Keep secret prompting conditional so repeat deployments remain non-interactive.
- Treat Podman, Python with venv, user systemd and basic archive/checksum tools as offline target prerequisites.
- Build platform-specific offline bundles on a machine compatible with the target.
- Verify every bundled artifact with SHA-256 before installation.
- Use Caddy's internal CA for offline-compatible local HTTPS without automatically modifying host trust.
- Persist Caddy's private CA material in a dedicated volume and expose only its public root certificate for explicit trust.
- Never commit secrets.
- Keep authentication authorization simple: all authenticated users may write all Todos; per-user ownership is outside this demo.
- Keep browser tokens in memory and use Authorization Code with PKCE S256 for the public frontend client.

## Local development

From the project root:

```bash
source backend/.venv/bin/activate
export DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD"
uvicorn backend.main:app --reload
```

From `backend/`:

```bash
source .venv/bin/activate
export DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD"
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000> in a browser.

## Roadmap

- M1: HTML/JavaScript + FastAPI with hardcoded Todo — completed
- M2: PostgreSQL + Todo CRUD — completed
- M2.5: Versioned SQL migrations — completed
- M2.6: Automated smoke and integration tests — completed
- M3: Containerfiles for backend and frontend — completed
- M4: Run manually with rootless Podman — completed
- M4.5: Troubleshooting and lifecycle — completed
- M5: Caddy reverse proxy — completed
- M5.5: End-to-end browser tests with Playwright for Python — completed
- M6: Quadlet and systemd — completed
- M7: Podman secrets — completed
- M8: Ansible deployment — completed
- M9: Offline installation from a `tar.gz` bundle — completed
- M10: HTTPS — completed
- M11: Keycloak authentication — completed

## Next step

Test the complete offline M11 bundle on a clean virtual machine.
