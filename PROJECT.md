# Project status

## Goal

Learn rootless Podman by building and deploying a small Todo application.

The project should demonstrate Podman, Quadlet, Ansible and eventually offline installation in small, understandable steps.

## Current milestone

M6 — Manage the containers with Quadlet and user systemd.

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
- M1 through M5.5 are complete.

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
- Never commit secrets.

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
- M6: Quadlet and systemd
- M7: Podman secrets
- M8: Ansible deployment
- M9: Offline installation from a `tar.gz` bundle
- M10: HTTPS
- M11: Keycloak authentication

## Next step

Define the network, PostgreSQL, migration, backend and frontend as rootless Quadlet user units with explicit startup ordering.
