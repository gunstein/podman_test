# Todo demo

FastAPI runs locally while PostgreSQL runs in rootless Podman.

## Start PostgreSQL

Choose a password for the current shell:

```bash
read -rsp "Database password: " TODO_DB_PASSWORD
echo
export TODO_DB_PASSWORD
```

Create persistent storage and start PostgreSQL:

```bash
podman volume create todo-postgres-data
podman run --name todo-postgres \
  --detach \
  --publish 127.0.0.1:5432:5432 \
  --env POSTGRES_DB=todo \
  --env POSTGRES_USER=todo \
  --env POSTGRES_PASSWORD="$TODO_DB_PASSWORD" \
  --volume todo-postgres-data:/var/lib/postgresql/data \
  docker.io/library/postgres:17
podman logs --follow todo-postgres
```

Stop following logs with Ctrl+C after PostgreSQL is ready. Later use `podman start todo-postgres` and `podman stop todo-postgres`. The volume preserves the data.

## Run FastAPI

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt
read -rsp "Database password: " TODO_DB_PASSWORD
echo
export TODO_DB_PASSWORD
export DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD"
python -m backend.migrate up
uvicorn backend.main:app --reload
```

Enter the same PostgreSQL password when prompted. Virtualenv activation and shell variables must be repeated in every new terminal. Do not store secrets in Git.

Open <http://127.0.0.1:8000>. API docs are at <http://127.0.0.1:8000/docs>.

## Database migrations

```bash
python -m backend.migrate status
python -m backend.migrate up
```

Roll back the latest migration:

```bash
python -m backend.migrate down
```

The initial rollback drops the `todos` table and deletes its data. Review every down migration before running it.

## Tests

Create the isolated test database once:

```bash
podman exec todo-postgres createdb -U todo -O todo todo_test
```

In an activated virtualenv, install test dependencies and run the tests:

```bash
python -m pip install -r backend/requirements-test.txt
export TEST_DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo_test user=todo password=$TODO_DB_PASSWORD"
python -m pytest backend/tests
```

The test suite refuses database names that do not end with `_test`. It migrates the test database up, clears Todos between tests and rolls the schema back afterward.

## End-to-end test

With the complete application running through Caddy, install Playwright and Chromium once:

```bash
python -m pip install -r backend/requirements-e2e.txt
python -m playwright install chromium
```

Run the browser test explicitly:

```bash
E2E_BASE_URL=http://127.0.0.1:8080 \
  python -m pytest e2e --browser chromium
```

The test creates, completes and deletes a uniquely named Todo through the browser. Chromium is a local test dependency and is not included in the application images or production bundle.

## Build container images

Build both images from the project root:

```bash
podman build --file backend/Containerfile --tag localhost/todo-backend:m5 .
podman build --file frontend/Containerfile --tag localhost/todo-frontend:m5 .
```

Smoke-test the images separately:

```bash
podman run --name todo-backend-smoke --rm --detach \
  --publish 127.0.0.1:18000:8000 localhost/todo-backend:m5
curl --fail --retry 10 --retry-delay 1 --retry-all-errors \
  http://127.0.0.1:18000/health
podman stop todo-backend-smoke

podman run --name todo-frontend-smoke --rm --detach \
  --publish 127.0.0.1:18080:8080 localhost/todo-frontend:m5
curl --fail --retry 10 --retry-delay 1 --retry-all-errors \
  http://127.0.0.1:18080/
podman stop todo-frontend-smoke
```

These smoke tests intentionally test each image in isolation. Full frontend-to-backend routing is tested through Caddy after all services are running.

## Run manually with rootless Podman

The existing PostgreSQL container was originally created without a named network. Recreate only the container on `todo-network`; the named volume keeps the database data:

```bash
podman network exists todo-network || podman network create todo-network
podman stop todo-postgres
podman rm todo-postgres
podman run --name todo-postgres \
  --detach \
  --network todo-network \
  --publish 127.0.0.1:5432:5432 \
  --env POSTGRES_DB=todo \
  --env POSTGRES_USER=todo \
  --env POSTGRES_PASSWORD="$TODO_DB_PASSWORD" \
  --volume todo-postgres-data:/var/lib/postgresql/data \
  docker.io/library/postgres:17
```

Removing the container does not remove `todo-postgres-data`. Use the same database password as before.

Apply migrations from a one-off backend container:

```bash
podman run --name todo-migrate --rm \
  --network todo-network \
  --env DATABASE_URL="host=todo-postgres port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD" \
  localhost/todo-backend:m5 \
  python -m backend.migrate up
```

Start backend and frontend containers:

```bash
podman run --name todo-backend --detach \
  --network todo-network \
  --publish 127.0.0.1:8000:8000 \
  --env DATABASE_URL="host=todo-postgres port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD" \
  localhost/todo-backend:m5

podman run --name todo-frontend --detach \
  --network todo-network \
  --publish 127.0.0.1:8080:8080 \
  localhost/todo-frontend:m5
```

Inspect and test the running containers:

```bash
podman ps
podman logs todo-postgres
podman logs todo-backend
podman logs todo-frontend
podman network inspect todo-network
curl --fail --retry 10 --retry-delay 1 --retry-all-errors \
  http://127.0.0.1:8000/health
curl --fail --retry 10 --retry-delay 1 --retry-all-errors \
  http://127.0.0.1:8000/ready
curl --fail --retry 10 --retry-delay 1 --retry-all-errors \
  http://127.0.0.1:8080/
```

Practice the lifecycle without deleting data:

```bash
podman stop todo-frontend todo-backend todo-postgres
podman start todo-postgres todo-backend todo-frontend
```

If backend health works but `/ready` or `/api/todos` fails with `No route to host`, inspect `podman exec todo-postgres cat /proc/net/route`. An empty table means the container network namespace is incomplete; `podman restart todo-postgres` recreates it.

The containers share a network, and the backend reaches PostgreSQL by the name `todo-postgres`. In the current M5 setup, the Caddy-based frontend handles HTTP routing as described below.

## Run through Caddy

Build the current frontend image, which contains both the static files and Caddy:

```bash
podman build --file frontend/Containerfile --tag localhost/todo-frontend:m5 .
```

Replace the stateless frontend container:

```bash
podman stop todo-frontend
podman rm todo-frontend
podman run --name todo-frontend --detach \
  --network todo-network \
  --publish 127.0.0.1:8080:8080 \
  localhost/todo-frontend:m5
```

Test every route through Caddy:

```bash
curl --fail --retry 10 --retry-delay 1 --retry-all-errors http://127.0.0.1:8080/
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
curl --fail http://127.0.0.1:8080/api/todos
```

Open <http://127.0.0.1:8080>. Caddy serves HTML, CSS and JavaScript directly and sends API, health and readiness requests to the backend. HTTPS remains disabled until M10.

## Run with Quadlet and systemd

Quadlet turns the files in `quadlet/` into user systemd services. The units keep the same container, network and volume names as the manual M4 setup.

First stop and remove the three manually created containers. This does not remove the named database volume:

```bash
podman stop todo-frontend todo-backend todo-postgres
podman rm todo-frontend todo-backend todo-postgres
```

Install the Quadlet files and create a private environment file:

```bash
mkdir -p ~/.config/containers/systemd ~/.config/todo-demo
cp quadlet/*.container quadlet/*.network quadlet/*.volume ~/.config/containers/systemd/
cp quadlet/todo.env.example ~/.config/todo-demo/todo.env
chmod 600 ~/.config/todo-demo/todo.env
```

Edit `~/.config/todo-demo/todo.env` and replace both example password values with the existing database password. The file is deliberately outside Git. M7 will replace this temporary arrangement with Podman secrets.

Reload user systemd and start the application:

```bash
systemctl --user daemon-reload
systemctl --user start todo-frontend.service
```

The `WantedBy=default.target` setting is handled by the Quadlet generator, so the generated service must not be enabled with `systemctl enable`. It will be included on future user-systemd starts. Starting the frontend now pulls in the complete dependency chain:

```text
PostgreSQL healthy -> migration completed -> backend started -> frontend started
```

The migration is a `oneshot` service. It waits for PostgreSQL's healthcheck and must finish successfully before the backend starts. Running it again is safe because applied migrations are recorded.

Inspect the units and logs:

```bash
systemctl --user status todo-postgres.service
systemctl --user status todo-migrate.service
systemctl --user status todo-backend.service
systemctl --user status todo-frontend.service
journalctl --user -u todo-postgres.service -u todo-migrate.service \
  -u todo-backend.service -u todo-frontend.service
```

Test the application at <http://127.0.0.1:8080>. Stop or restart the frontend service:

```bash
systemctl --user stop todo-frontend.service
systemctl --user restart todo-frontend.service
```

Stopping the frontend does not automatically stop its dependencies. To stop every application service explicitly:

```bash
systemctl --user stop todo-frontend.service todo-backend.service \
  todo-migrate.service todo-postgres.service
```

User services normally start when the user logs in. To allow them to start during boot without an interactive login, an administrator can enable lingering once:

```bash
sudo loginctl enable-linger "$USER"
```

This host-level choice is optional for local learning and is not performed by the project.

## API

- `GET /health`
- `GET /ready`
- `GET /api/todos`
- `POST /api/todos`
- `PUT /api/todos/{todo_id}`
- `DELETE /api/todos/{todo_id}`
