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

## API

- `GET /health`
- `GET /api/todos`
- `POST /api/todos`
- `PUT /api/todos/{todo_id}`
- `DELETE /api/todos/{todo_id}`
