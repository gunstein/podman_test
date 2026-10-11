# Guestbook

A third database-and-login app for the platform. Its API is Python/FastAPI;
its frontend is plain HTML, CSS and JavaScript. PostgreSQL holds `entries`
(`id`, `message`, `created_at`). There are no uploads or other persistent files.

All entry operations require login: `GET /api/entries`, `POST /api/entries`
with `{"message": "Hello"}`, and `DELETE /api/entries/{id}`. Any logged-in
user can delete any entry; there is no row ownership. `/health` and `/ready`
are public. Readiness queries the migrated table as the runtime role.

`app.yaml` requests the database and the `guestbook-frontend` client with
`guestbook-api` as its access-token audience. The pod's init container runs
migrations as `guestbook_migrator`; the API uses `guestbook_app`, with only
SELECT, INSERT, UPDATE and DELETE on entries (the API exposes no update operation). The platform creates the roles and
secrets. The frontend receives only `FRONTEND_LOGIN` from the ConfigMap.

## Run from the repository root

On a development host with rootless Podman 5+, Python, Jinja2 and PyYAML:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer platform hostnames --environment local
# Map the printed hostnames to 127.0.0.1, including guestbook.test.
PYTHONPATH=deploy/installer python3 -m app_installer install --mode dev --deployment-mode build
```

Trust the proxy CA as described in `docs/TLS.md`, then visit
`https://guestbook.test:8443`. Use an existing Keycloak user (the realm is
still shared), or provision the documented development test user with
`e2e/provision_user.py`. Sign in, write a message, refresh and delete it.
Anonymous `GET /api/entries` must return 401. The installer checks that too.

Build the offline bundle with `deploy/offline/build-bundle.sh` as described
in `deploy/offline/README.md`. Stop development with:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer down
```

The operator's `platform.yaml` assigns replication port 5435: 5434 belongs
to Keycloak. Adding this database changes the DR group. Existing DR hosts
need the documented standby rebuild and replication firewall change; the
phase 5-4 maintainer must extend the lab's markers and acceptance checks.

## Tests

```bash
python3 -m pip install -r examples/guestbook/backend/requirements-test.txt
python3 -m unittest discover -s examples/guestbook/backend/unit_tests -v
```

These database-free tests cover API authentication, token signature/issuer/
audience/expiry, rejection of ID tokens and another app's token, input
validation, bound SQL parameters, readiness and migration startup retries.
They do not replace a real database, browser login or DR acceptance run.
