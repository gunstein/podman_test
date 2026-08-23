# Project status

## Goal

Learn rootless Podman by building and deploying a small Todo application.

The project should demonstrate Podman, Quadlet, Ansible and eventually offline installation in small, understandable steps.

## Current milestone

M4 — Run the application manually with rootless Podman.

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
- Six automated tests cover health, frontend serving, CRUD, validation, missing Todos and migration file pairs.
- Tests were verified against an isolated, automatically removed PostgreSQL test container.
- Backend and frontend images build successfully and pass isolated smoke tests as UID 1000.
- M1, M2, M2.5, M2.6 and M3 are complete.

## Decisions

- Implement and understand the application before containerizing it.
- Use rootless Podman.
- Run containers manually before introducing Quadlet and Ansible.
- Prefer simple, pedagogical solutions over abstraction.
- Keep dependencies and Bash scripts minimal.
- Run PostgreSQL in a rootless Podman container during M2 instead of installing it on the host.
- Keep FastAPI running locally through M2; containerize it in M3.
- Add Keycloak last.
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
- M4: Run manually with rootless Podman
- M4.5: Troubleshooting and lifecycle
- M5: Caddy reverse proxy
- M5.5: End-to-end browser tests with Playwright for Python
- M6: Quadlet and systemd
- M7: Podman secrets
- M8: Ansible deployment
- M9: Offline installation from a `tar.gz` bundle
- M10: HTTPS
- M11: Keycloak authentication

## Next step

Run PostgreSQL, backend and frontend manually with rootless Podman and learn their lifecycle and networking.
