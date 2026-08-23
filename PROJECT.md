# Project status

## Goal

Learn rootless Podman by building and deploying a small Todo application.

The project should demonstrate Podman, Quadlet, Ansible and eventually offline installation in small, understandable steps.

## Current milestone

M3 — Build Containerfiles for the backend and frontend.

## Completed

- `GET /health` returns `{"status": "ok"}`.
- PostgreSQL runs in a rootless Podman container with a named volume.
- The Todo API supports create, read, update and delete operations.
- The frontend uses plain HTML, CSS and JavaScript.
- The frontend can create, complete and delete Todos.
- FastAPI serves the frontend.
- The README contains local development instructions.
- PostgreSQL data was verified to survive a container stop and restart.
- M1 and M2 are complete.

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
- M3: Containerfiles for backend and frontend
- M4: Run manually with rootless Podman
- M4.5: Troubleshooting and lifecycle
- M5: Caddy reverse proxy
- M6: Quadlet and systemd
- M7: Podman secrets
- M8: Ansible deployment
- M9: Offline installation from a `tar.gz` bundle
- M10: HTTPS
- M11: Keycloak authentication

## Next step

Design and build separate Containerfiles for the backend and frontend without orchestrating the full application yet.

## Deferred work

- Add proper, versioned PostgreSQL schema migrations after the initial CRUD and database schema are understood and stable. Start M2 with simple, explicit table initialization to keep the learning steps small.
