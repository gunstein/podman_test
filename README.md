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
python -m pytest
```

The test suite refuses database names that do not end with `_test`. It migrates the test database up, clears Todos between tests and rolls the schema back afterward.

## Build container images

Build both images from the project root:

```bash
podman build --file backend/Containerfile --tag localhost/todo-backend:m3 .
podman build --file frontend/Containerfile --tag localhost/todo-frontend:m3 .
```

Smoke-test the images separately:

```bash
podman run --name todo-backend-smoke --rm --detach \
  --publish 127.0.0.1:18000:8000 localhost/todo-backend:m3
curl --fail --retry 10 --retry-delay 1 --retry-all-errors \
  http://127.0.0.1:18000/health
podman stop todo-backend-smoke

podman run --name todo-frontend-smoke --rm --detach \
  --publish 127.0.0.1:18080:8080 localhost/todo-frontend:m3
curl --fail --retry 10 --retry-delay 1 --retry-all-errors \
  http://127.0.0.1:18080/
podman stop todo-frontend-smoke
```

The M3 images are intentionally tested in isolation. The frontend container serves static files, but its relative `/api` requests are not routed to the backend until the reverse proxy milestone.

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
  localhost/todo-backend:m3 \
  python -m backend.migrate up
```

Start backend and frontend containers:

```bash
podman run --name todo-backend --detach \
  --network todo-network \
  --publish 127.0.0.1:8000:8000 \
  --env DATABASE_URL="host=todo-postgres port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD" \
  localhost/todo-backend:m3

podman run --name todo-frontend --detach \
  --network todo-network \
  --publish 127.0.0.1:8080:8080 \
  localhost/todo-frontend:m3
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

The containers share a network, and the backend reaches PostgreSQL by the name `todo-postgres`. The frontend is still isolated at HTTP routing level; Caddy will route its relative `/api` requests in M5.

## API

- `GET /health`
- `GET /ready`
- `GET /api/todos`
- `POST /api/todos`
- `PUT /api/todos/{todo_id}`
- `DELETE /api/todos/{todo_id}`
