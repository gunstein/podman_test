# Add an app with the current platform contract

An app is a directory containing `app.yaml`, `pod.yaml.j2` and its own
source, plus one entry in the operator's `platform.yaml`. Start with
[Guestbook](../examples/guestbook/README.md) for PostgreSQL and login, or
[Help](../examples/help/app.yaml) for static pages. These are runnable
examples; this page describes the implemented schema, not the plan's future
fields. App code does not import another app or the installer.

## 1. Describe the app

This is Guestbook's complete supported shape for `app.yaml`:

```yaml
name: guestbook
database: true
login:
  client: guestbook-frontend
  audience: guestbook-api
images:
  backend:
    context: backend
  frontend:
    context: frontend
endpoints:
  frontend:
    port: 8080
  backend:
    port: 8000
routes:
  - path: /api/
    to: backend
  - path: /health
    exact: true
    to: backend
  - path: /ready
    exact: true
    to: backend
  - path: /
    to: frontend
ready: /ready
checks:
  - path: /health
    status: 200
  - path: /api/entries
    status: 401
```

Choose a unique `name` (a lower-case letter followed by lower-case letters
or digits, at most 30 characters). `identity` and `keycloak` are reserved
for the platform's identity service. App hostnames must be unique and must
not equal `identityHostname`. The model checks these rules for source
configuration and offline bundles alike. `name`, `images`, `endpoints` and
`routes` are required. `database` defaults to false; omit `login` for an app
without login. `ready` and `checks` are optional. Without `ready`, the
platform waits for the pod's regular containers to run and, where declared,
be healthy. Unknown fields are refused: no `setup`, `realm`, route
`container` or `rewrite` fields exist today.

Each image's `context` is relative to the app directory; its optional
`containerfile` defaults to `Containerfile` inside that context. Add a
`.containerignore` there, as the backends do, to exclude local environments,
bytecode and tests. Endpoints name pod ports. Routes name an endpoint with
`to`; `exact` defaults to false. `/auth` belongs to the platform. A pod's
containers share ports, so assign distinct ports inside it.

`ready` is an anonymous GET that must return 200, normally after checking
the migrated table through the runtime role. Every `checks` entry is also
an anonymous GET; redirects are not followed. Guestbook deliberately
expects **401** for `/api/entries`, since reads require login. That proves
the anonymous boundary and route, not authenticated CRUD. Test real login
and CRUD separately. Decide and document row ownership: Guestbook lets
any signed-in user read and delete any entry and stores no user identity.

## 2. Bring the pod and database migration

Copy the complete [Guestbook pod template](../examples/guestbook/pod.yaml.j2)
into your app directory and adapt the processes and ports. Its names and
images already come from the app model. Use [Help's template](../examples/help/pod.yaml.j2)
for a static app. The template receives exactly `app` and `images`:

| Expression | Guestbook value / purpose |
|---|---|
| `app.name`, `app.pod` | `guestbook`, `guestbook-app` |
| `app.names.resource("backend")` | `guestbook-backend`; use the same pattern for frontend and migration containers |
| `images.backend`, `images.frontend` | Built image references for the names declared in `images` |
| `app.names.resource("backend-config")` | `guestbook-backend-config`, the platform's ConfigMap |
| `app.names.kube_secret("backend")` | `guestbook-kube-backend-secret`, runtime password file |
| `app.names.kube_secret("migrator")` | `guestbook-kube-migrator-secret`, migration password file |
| `app.database.role("app")` | `guestbook_app` |
| `app.database.role("migrator")` | `guestbook_migrator` |
| `app.database.container` | `guestbook-postgres`, reachable on the rootless network |

The backend's `envFrom` reads the ConfigMap. For a database app it contains
`DATABASE_HOST`, `DATABASE_PORT` (5432 inside the network) and `DATABASE_NAME`.
It also contains `LOG_LEVEL`; with login it adds `OIDC_ISSUER`,
`OIDC_JWKS_URL`, `OIDC_AUDIENCE` and `FRONTEND_LOGIN`.

The pod sets `DATABASE_USER` and `DATABASE_PASSWORD_FILE` separately for
migration and runtime. Each secret mounts a file named `database-password`;
the example uses `/run/secrets/<app>-migrator/database-password` for the
init container and `/run/secrets/<app>-backend/database-password` for the
backend. Mount only the runtime secret in the backend. Put no password in
YAML, an image, a command line or Git.

The platform creates the bootstrap, migrator and runtime roles and their
base rights. Your migration creates tables as `<app>_migrator`, then grants
only the required table and sequence privileges to `<app>_app` (Guestbook's
[migrations](../examples/guestbook/backend/migrations) show both). Runtime
must be denied DDL and access to the migration table. Keep migrations and
the runner with the app; a failed migration init must prevent app startup.
Use a new migration for a later change to an already-delivered schema.

The pod must render exactly one Pod, use app-prefixed container names and
declared images, declare endpoint ports and memory limits, and request no
host access or extra privileges. Supported pod volumes are the app's own
secrets/ConfigMap and ephemeral `emptyDir`; persistent app data belongs in
PostgreSQL. `pod_contract.py` checks this during loading and rendering.

For login, choose distinct, unique `login.client` and `login.audience`.
Mount **only `FRONTEND_LOGIN`** as `/config/login.json` in the frontend,
never the whole ConfigMap. Use the adapter behind `auth.js`, and enforce
signature, issuer, API audience, expiry and subject in the backend.
`login:` configures a client; it does not protect endpoints. See
[Identity](IDENTITY.md) for the file-by-file chain and token tests.

## 3. Register, build and verify

The repo already registers Guestbook with:

```yaml
apps:
  # Other app entries stay here too.
  - path: examples/guestbook
    hostname: guestbook.test
    replicationPort: 5435
```

This is an entry in the existing `platform.yaml`, not a complete replacement
file. Paths are relative to that file and may point outside the repository.
`replicationPort` is required exactly when `database: true`; it must be
unique and in 1024–65535. **5434 is reserved for Keycloak's PostgreSQL**.
This repo uses 5432 for Todo, 5433 for Notes and 5435 for Guestbook. Avoid
nginx's loopback port 8080 and the configured public HTTPS port (8443 here).
Add each new database's port to the peer-only replication firewall rules.
Adding a database changes the complete DR group: extend the lab's markers,
failover, backup/restore, PITR and standby rebuild/re-seed proofs together.

The lab derives its database group and replication ports from `platform.yaml`.
Its application data and browser checks are explicit. For a new database
app, make these changes together:

| File | What to extend |
|---|---|
| `deploy/scripts/lab/acceptance.py` | Add the app origin and browser environment in `check_browser` and `do_markers`; record its marker ID in `do_markers`, query its table in `check_markers`, and add a rolled-back insert in `check_write_probe`. |
| `e2e/create_markers.py` | Create a persistent row through the app's UI, read its ID with the appropriate authentication and include it in the output parsed by `do_markers`. |
| `e2e/test_multi_app.py` | Exercise login, write/read/delete, frontend-config isolation and rejected tokens for the new app, before and after failover. |
| `docs/ACCEPTANCE.md` and `docs/ACCEPTANCE-AGENT.md` | Extend service/database tables, later-row snapshot-restore checks, before/after PITR rows, separate restore/live SQL assertions and restore-container cleanup. Add the port's open, blocked and post-rebuild checks; review the firewall ranges for any gaps between allocated ports. |
| `tests/test_acceptance_tool.py` | Extend marker creation/readback and write-probe fixtures. `test_marker_checks_cover_the_real_platform_database_apps` compares the databases actually queried for markers with the checkout's database apps, so an omitted app fails. |
| `tests/test_acceptance_guide.py` and `tests/test_acceptance_step.py` | Cover the new guide commands, PITR assertions and step names/order; preserve refusal and confirmation gates. |
| `e2e/test_create_markers.py` and `.github/workflows/clean-install.yml` | Cover the new marker read contract and run the app's unit/role tests; full-stack CI runs the multi-app flow and marker helper. |

Coverage is complete only when the app's own row is checked after failover,
nightly backup/restore, rebuild, re-seed and final reboots, and its separate
PITR proof checks both restored and live data. Existing `check_markers`
calls cover these stages once its app query is added. The marker-coverage
test detects an omitted database; real browser CI proves marker creation,
and the full lab run proves recovery on the target hosts. The tests do not
replace that run.

From the repository root, with build dependencies installed:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer platform hostnames --environment local
deploy/scripts/render-kube-runtime.sh local /tmp/platform-app-preview
python3 -m unittest discover -s examples/guestbook/backend/unit_tests
```

For your app, use its test path. On a rootless Podman host, map the printed
hostnames, run `deploy/scripts/dev/dev-up.sh`, trust the serving CA and
exercise the app in a browser. Stop with `deploy/scripts/dev/dev-down.sh`.
Build the offline bundle using [the offline guide](../deploy/offline/README.md).
An offline target needs Python and the rendered bundle, never Jinja2.

Platform behaviour tests use the fixed Todo/Notes/Help platform in
`tests/fixtures/platform.yaml`, through `tests/platform_fixture.py`.
Do not add a new app to that fixture just because the checkout gains one.
Tests deliberately using the checkout's real app set are:

- `test_render_baseline.py` / `render_baseline.py`: the rendered manifests,
  units and bundle metadata; run `python3 tests/render_baseline.py --update`
  only for an intended change and explain its diff.
- `test_acceptance_guide.py` and the guide-command checks in
  `test_acceptance_tool.py`: the documented services, databases and commands.
- `test_deploy_layout.py`, the package tests in `test_offline_scripts.py`
  and `test_operations_distribution.py`: the real shell entrypoints and
  packages, with file sets derived from the configuration.
- Full-stack CI and `e2e/`: build/install all configured apps and explicitly
  exercise each example's login, API and data. Add the new app's backend
  unit/role tests and browser flow to `.github/workflows/clean-install.yml`.

Before pushing, stage the files, then run `ruff check .`, `pyright` and
`PYTHONPATH=deploy/installer:deploy/dr:tests python3 -m unittest discover -s tests`.
Wait for green CI. The lab acceptance is a separate proof on the target hosts.
