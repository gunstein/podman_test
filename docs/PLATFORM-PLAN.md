# Platform plan: from the Todo demo to a small reusable app platform

Status: **version 2, being implemented one phase at a time** on
`feature/platform`; section 10 records what each phase has done. Sections 4
to 9 describe the target: where they say "today", they mean the code before
phase 0. This one document holds scope, rules, contracts and phases; version 1 of the
plan and the separate design document are in Git history (`836a176`).
Version 2 follows an external review: one resolved model, explicit
contracts, a narrower first version, and simplicity as an acceptance
criterion.

## 1. The overriding requirement

> Maintainability for a moderately experienced Python developer is an
> overriding requirement. The goal is a small, reusable Podman platform with
> a clear control flow and few abstractions. The first version supports
> today's apps and one static app. We do not build general support for
> unknown future services. Every new abstraction must be justified by a
> concrete need in existing code and must make common changes easier. It is
> acceptable that new kinds of services later require bounded changes in
> the core. The work is split into small deliveries, and understandability
> is assessed after each phase.

Where anything below conflicts with this, this wins.

## 2. Scope of the first version

In scope:

- **Login apps with a database**, as Todo and Notes are today: frontend and
  backend in one pod, PostgreSQL in its own pod, login through the shared
  Keycloak.
- **One static app**: Help, with no login, backend or database; served on its
  own hostname first and under another app's path (`/help/`) last.
- **PostgreSQL is the only storage with DR support**, through today's
  replication, promotion, backup, PITR and rebuild of the whole group.
- One shared Keycloak on its own hostname; each app chooses a realm, shared
  or its own.
- **One installation per Unix user.** The fixed `platform-` prefix names
  shared resources; two installations under one user are not supported.
- Apps may live in a directory outside the repository.

Not in scope, deliberately:

- Unknown future service kinds, generic component or feature mechanisms,
  plugins. A new kind of service later may need bounded core changes.
- Storage other than PostgreSQL with DR. A future service with another
  storage engine needs its own DR implementation.
- Apps with a backend but no database (nothing prevents it later).
- Arbitrary `.kube` overrides, realm import files, multiple installations
  per user, Kubernetes, Docker, Compose, automatic HA.

What does not change: rootless Podman, Kubernetes YAML as a Podman workload
format, `podman kube play/down` for development, Quadlet `.kube` units under
user systemd for production, offline bundles with rendered YAML, the DR
safety boundaries, external secrets.

## 3. Simplicity rules

These rules apply to planning and to every code review.

| Rule | In practice |
|---|---|
| An abstraction needs an existing need | No extension mechanism because it may be useful later. |
| Prefer functions and dataclasses | A class only when it gives a clear responsibility, never to build a framework. |
| Keep the control flow visible | Easy to follow: validate → prepare → install → start → check. |
| No hidden behaviour | No dynamic imports, plugin discovery, metaclasses or registration by import side effects. |
| One model, few layers | YAML → loader → `Platform` → the functions that act. No near-identical representations, no adapter chains. |
| Allow some duplication | A few similar lines can beat an abstraction with many options. |
| Bounded configuration | YAML describes supported choices; it is not a programming language. |
| Small deliveries | Each change has one purpose and can be reviewed without understanding the whole refactoring. |

No class hierarchy is decided in advance. Optional capabilities of an app
are optional fields (`app.has_database`, `app.has_login`, since phase 4c-1);
code says `if app.has_database:`. A shared interface is introduced only when two or more
concrete implementations show what is actually common.

**Understandability check, after every phase.** A developer or an agent
without the implementation history does three tasks:

1. Explain what happens when an installation starts.
2. Find where a new app is registered and how it gets routes and images.
3. Make a small behaviour change and find the right test.

If this requires following many indirect calls or learning an internal
framework, simplify before the next phase. Documentation does not
compensate for needlessly complicated code. The result is recorded with
the phase.

## 4. One resolved model

Where it stands after phase 4f-3: `apps.Platform` has two fields, the apps in
start order and Keycloak's default hostname, and derives the rest (the DR
group, workloads, services, and `database_apps`, `login_apps` and
`has_identity`, so PostgreSQL and Keycloak run only when an app needs
them); each app holds whether it has a database and login, the images it
builds (`apps.AppImage`), its endpoints and routes (`apps.Endpoint`,
`apps.Route`), and its ready path and checks (`apps.Check`, run by
`checks.py`); each workload names its unit template and the services it
requires (`apps.Workload`), from which every unit is rendered; and each app
has its own pod template (`App.pod_template`), checked once rendered
(`pod_contract.py`). A build reads it from `platform.yaml` and each app's
`examples/<app>/app.yaml` (`platform_file.load`, which also gives the
environment's port and log level); there is no list of apps in the code.
`bundle.json` (format version 14) carries it, and a host records it in
`~/.config/platform/platform.json` with the same format version. The richer tree below grows with phases
4 and 5 (images, routes, checks, database and login as app fields).

Before phase 2, bundling, installation and DR each rebuild the installation from the
Python registry (`apps.APPS`, `apps.IDENTITY_APP`, default arguments such as
`workloads(applications=APPS)` and `setup_roles(app=apps.APPS[0])`);
`target_render.py` uses the registry even when it reads `bundle.json`.
Writing more to JSON is not enough: the readers must stop reconstructing.

- `Platform` is one frozen dataclass tree: apps, workloads in start order,
  images, routes, secret names, the replicated database group, realms and
  clients, setup tasks and checks. It is the **only** description of an
  installation.
- **Build mode** creates it from `platform.yaml` and the apps' `app.yaml`
  (`load_platform(path) -> Platform`), validates it, and writes it into
  `bundle.json` (`platform_to_json`).
- **Offline install and DR hosts** read it back (`platform_from_json`),
  standard library only, and pass it to the same functions build mode uses.
- Every function that acts takes the `Platform` (or a part of it) as an
  argument. No YAML loading at import time, no module-level app lists, no
  default arguments that bind an app list. Two different platforms in one
  Python process must not share state (tested).
- **Versions**: `bundle.json` keeps `format_version` (14 since phase 5-1b); the
  installer and the DR tools refuse any other. Since phase 4i the bundle
  records the ID of every image it carries (`images/image-ids.json`), and an
  offline install or a DR host loads an image whose ID on the host is
  another, so a new bundle under the same tag (`settings.IMAGE_TAG`) runs
  its own images.

## 5. Contracts

Each contract is short, documented next to the code that enforces it, and
tested.

### 5.1 Configuration files

Implemented so far (phases 3 to 5-1b): `platform.yaml` with
`identityHostname`, `publicPort`, `logLevel`, the `local` and `prod`
environments and each app's `path`, `hostname` and, exactly when the app
has a database, `replicationPort`; `app.yaml` with `name`, `database`
(true or false, default false), `login` (optional: its `client` and its
API `audience`, two different words; without it the app has no login),
`images` (each built image's `context` and
`containerfile`, section 5.2), `endpoints` (each one's `port`), `routes`
(`path`, `to`, `exact`, section 5.3), `ready` and `checks` (`path`,
`status`, section 5.4); beside `app.yaml`, the app's own pod template
`pod.yaml.j2` (section 5.9). Everything else below comes with the phase that
needs it. Since 5-2 the platform makes an app's database roles
(`app_installer.database_roles`; no image of the app's has a fixed name),
and the app's pod runs its own migrations, which grant the runtime role
`<app>_app` its table rights.

- `platform.yaml` is the operator's: hostnames, which realm each app uses,
  each database's `replicationPort`, which app serves another app's help
  path, environments (`publicPort`, `logLevel`).
- `app.yaml` is the app developer's: images, HTTP endpoints and routes,
  whether it needs a database or login, the help path it wants, its setup
  command and checks. It never names other apps or installation choices.
- Relative paths resolve from the directory of the file that contains them.
  Unknown fields are errors. An environment may override only `publicPort`
  and `logLevel`. No templating inside YAML.
- `publicPort` must be 1024–65535 (rootless; the same rule DR already
  enforces in `promoted.py`). Examples use 8443.

### 5.2 Images

- Each image is either built (`name`, `context` relative to the app
  directory, `containerfile` relative to the context) or prebuilt
  (`reference`, pulled when the bundle is built). Init containers and setup
  jobs use declared images only. Phase 4a built the first kind; prebuilt
  images come with the first app that needs one.
- The model decides exactly which images the bundle carries. A static-only
  installation carries nginx, Keycloak only if some app needs login, and
  PostgreSQL only if some app needs a database (`images.shared_images`,
  phase 4c-1).

### 5.3 Endpoints and routes

- The app declares named endpoints (`frontend: {container: frontend, port:
  8080}`) and routes on its own hostname (`/` → `frontend`, `/api/` →
  `backend`). The operator chooses the hostname. A static app declares its
  `/` route itself; no route depends on login.
- A route has a few explicit fields: `path` (prefix match), `to` (an
  endpoint), `rewrite` (`keep` or `strip`; `/help/` → `/<app>/` is
  `strip` plus a target prefix), and a header policy (`app` or `static`).
- One hostname may have many paths; the same hostname and path twice is an
  error. Tested together: `/`, `/api/`, `/help/`, with and without trailing
  slash, static assets and redirects.
- Phase 4b built `path`, `to` and one field more than this list: `exact`,
  for the exact-match `/health` and `/ready` of today's apps. A prefix path
  ends with a slash, `/auth` is the platform's (Keycloak), and a path holds
  only characters that cannot change nginx.conf. An endpoint has only a
  `port` so far (the container comes with pod templates, 5.9); `rewrite` and
  the header policy come with Help (phase 4f), which needs them.

### 5.4 Start, readiness, setup and checks

Four separate things, all in the model, used the same way by development,
production and DR:

1. **Start order**: each workload's `requires`. Generated `Requires=` and
   `After=`, and the `kube play` order in development, come from it. Missing
   references and cycles are validation errors.
2. **Readiness**: when a workload is ready (a container health check, or an
   HTTP path that must answer).
3. **Setup tasks**: one-shot commands with an image, a command and when they
   run (after the database is ready, after the app has started). They
   replace `install.setup_roles()` running `backend.setup_roles` by
   convention.
4. **Checks**: declared HTTP requests with an expected status, run after
   install and by DR after promotion. They replace
   `promoted.require_application()` expecting a list from `/api/...`.

Phase 4c-2 built readiness as an HTTP path (`ready`, optional: an app
without one is ready when its containers run, and are healthy where they
have a health check, as `wait-ready.sh` asks since phase 4h) and checks as GET requests
with a status (`checks`, optional), both on the app's own hostname through
nginx and each matched by one of its routes. Container health checks stay
in the pod templates (5.9); setup tasks come with phase 5.

Phase 4d built the start order: each `apps.Workload` has its `requires`
(an app needs its database and, with login, Keycloak; Keycloak its
database; nginx every app and Keycloak), and its unit's `Requires=` and
`After=` are rendered from them; `Platform.workloads()` lists them in an
order where each requires only earlier ones (tested), and development
mode's `kube play` and every start follow that order. The model makes them,
not YAML, so there is nothing for a user to get wrong (no missing
reference or cycle to report).

Identity setup waits only for Keycloak, never for the apps; app checks run
after identity setup. This removes the possible cycle where identity setup
waits for an app that waits for identity.

### 5.5 Database roles

The **platform** creates the three roles (bootstrap owner `<app>`, migrator
`<app>_migrator`, runtime `<app>_app`), their secrets, `CONNECT` and schema
usage; the **app's migrations** grant the runtime role its table rights
(the migrator owns the tables). Least privilege stays per table, and the app
ships no role code. Built in 5-2: `app_installer.database_roles` runs its
SQL through the database's own `psql` (`podman exec`, the passwords on
stdin), once per install after the database is healthy and before the app
starts; it never grants or revokes table rights, so a migration's grants
are kept on every install.

### 5.6 Data and DR

- Version 1 knows two data classes: **PostgreSQL in the replicated group**
  and **no persistent data**. Every volume in an app's pod template must be
  declared and classified.
- Anything else (local files, uploads, caches) is rejected when DR is set
  up, unless the operator sets an explicit, documented loss acceptance for
  that volume in `platform.yaml`. A rebuildable-cache class is added when
  an app needs it.
- **Membership of the DR group is explicit.** Adding an app with a database
  changes the group; app-ops refuses DR operations while the standby's group
  differs from the model, and the documented way to change it is a standby
  rebuild (today's operation).

### 5.7 Keycloak

- The platform owns: each realm's existence, the login protection fields
  (`REALM_SECURITY`), and one client per login app (client ID, redirect URIs,
  web origins, audience mapper). It never overwrites other fields; users,
  themes and other settings belong to the operator in Keycloak.
- Setup is idempotent: create what is missing, correct owned fields, report
  what changed. A partial failure is fixed by running again.
- No realm import files in version 1, so users and secrets never travel in
  bundle metadata. Clients are created from platform defaults; copying the
  Todo client goes away.
- DR verifies every realm's issuer and each client after promotion and only
  corrects owned fields; it never recreates a realm or touches users.
- Built in 5-1b: every client is made from the platform's defaults (no
  client is copied, and the realm import holds none), and an existing
  client's owned fields are corrected on every install and promotion:
  public, the code flow only, PKCE S256, its redirect URI and web origin,
  and exactly one audience mapper with the app's API audience in access
  tokens only. Still for phase 6: one realm per choice, no realm import
  file, DR verification per realm.

### 5.8 Adding, changing and removing apps

- Adding: a new directory and entry; install renders, installs and starts
  it. If it has a database, see the DR group rule (5.6).
- Removing an app from `platform.yaml` stops and removes its units and
  YAML, and **keeps** its database volume, secrets and Keycloak client. A
  separate, explicit command purges them. Until that is built (phase 7),
  install refuses a platform that leaves out a workload the host has
  installed, and names its service: an app, an app's database
  (`database: false`), or Keycloak when no app has login any more (phase 4h,
  widened from apps to workloads in 4i).

### 5.9 App pod templates

- Apps keep their own `pod.yaml.j2`, rendered with documented variables
  (names, images, secret names, OIDC values, database host).
- After rendering, before bundling and before any side effect in a direct
  install, the installer validates: pod and container names match the
  model, only declared images, only the app's own secrets, only declared
  and classified volumes, no `privileged`, `hostNetwork`, `hostPID`,
  `hostPath` or added capabilities, resource limits present.
- This checks the platform contract for **trusted** app packages. It is not
  a sandbox: it does not make arbitrary templates or images safe.
- `.kube` units are generated from the model. No app overrides in version 1;
  a declarative setting (such as a start timeout) is added when needed.
- Phase 4f-1 built the template and its check: `pod.yaml.j2` beside the
  app's `app.yaml`, rendered with `app` (the App: `app.pod`, `app.names`,
  `app.database`) and `images` ({name: reference}), then held by
  `pod_contract.check` to: exactly one Pod named `app.pod`; containers named
  `<app>-...` running only declared images; no `hostNetwork`, `hostPID`,
  `hostIPC`, `privileged` or added capabilities; a memory limit on each
  container; only the app's own secrets and ConfigMap, and otherwise only
  `emptyDir` volumes (an app's data is in PostgreSQL, 5.6); and a container
  port for each endpoint. A build install renders and checks before the
  host records anything. An app without a database or login gets the same
  variables; its ConfigMap holds only `LOG_LEVEL` (phase 4f-2). Not yet:
  resource limits other than memory.

### 5.10 Dependencies by host role

| Role | Needs |
|---|---|
| Build host | Python, Jinja2, PyYAML, Podman |
| Offline target (installer) | Python standard library, Podman |
| DR host (`app_dr_host`) | Python standard library and PyYAML, Podman |
| Controller (`app-ops`) | Python and PyYAML (inventory), SSH |

## 6. Names

- **platform**: the shared prefix (`platform.yaml`, `platform-proxy`,
  `platform-offline-<tag>.tar.gz`, `~/.config/platform`). Fixed in code,
  not the project's name.
- **app**: a user workload with its own `app.yaml` (`todo`, `notes`, `help`).
  "Service" is avoided because of systemd's `*.service`.
- Keycloak is `identity`, nginx is `proxy`. They are fields of `Platform`,
  not instances of a generic component type.

## 7. Phases

Each phase: one purpose, all tests green, a working offline install, and the
understandability check (section 3) recorded. Phases marked *behaviour*
change what is installed and end with a full acceptance run.

A full acceptance run takes hours, so it runs once at the end of a phase,
not after each of its steps (5-1, 5-2, ...). Between runs CI carries the
steps: the full-stack job against real Keycloak and PostgreSQL, the browser
test and the unit tests. A step's phase log names what only the lab can show
(Quadlet, the offline bundle, DR, backup, PITR, reboot), and the phase's run
checks those. An extra run in the middle of a phase is for a change to
fencing, promotion, backup, PITR or standby rebuild that CI cannot reach.

**Phase 0: Baseline.**
Fixtures of today's rendered YAML, units and `bundle.json` (build mode and
offline bundle). Make `tests/test_dr_boundary.py` recursive (today it uses
`glob("*.py")`, top level only). A name guard (`todo`/`notes` outside the
example apps) as support only; it does not prove generality. Update
AGENTS.md: the new goal, section 1 and 3 as rules, and the FastAPI and
plain HTML/JS requirements scoped to the example apps.

**Phase 1: Names and identity hostname.** *Behaviour.*
`todo-` shared names become `platform-` (proxy image and archive, TLS
secrets and volume, bundle and operations package, `~/.config` and
`~/.local/state` directories, DR timers). Keycloak gets its own hostname
instead of `IDENTITY_APP`'s: proxy, certificate names, issuer, `TARGET_*`
placeholders, failover and promotion checks.

**Phase 2: The model, from today's registry.**
Introduce `Platform` and build it from today's Python registry (no YAML
yet). Pass it explicitly everywhere; remove module-level app lists and
binding default arguments; `target_render.py` and DR read it from
`bundle.json`. Test two platforms in one process. Rendered output equals
phase 1's.

**Phase 3: The model, from YAML.**
`platform.yaml` and `examples/<app>/app.yaml` with today's values; the
loader and its validation messages. Delete the Python app list and
`deploy/environments/*/values.yaml`. Rendered output unchanged.

**Phase 3b: Scripts read the model.** `wait-ready.sh`, `preflight.sh`,
acceptance (`acceptance.py`, `acceptance_preflight.py`), `run-e2e.sh`, the
clean-install workflow and the replication firewall range read the model
(the bundle's or the host's record, through a small explicit Python
command) instead of hand-kept lists. Moved forward from 4e after the phase 1
understandability check, where these copied lists were the main friction;
the phase 2 check found them again.

**Phase 4: A static app end to end.** The first proof of generality, split
in small deliveries, each driven by what Help needs:
- 4a images contract (5.2), including external context paths;
- 4b endpoints and routes (5.3), nginx rendered from routes;
- 4c start, readiness and checks in the model (5.4), with Keycloak and
  PostgreSQL only when needed: 4c-1 database and login as app fields, and
  PostgreSQL and Keycloak only when an app needs them; 4c-2 readiness and
  checks;
- 4d generated `.kube` units and `kube play` order from the model;
- 4e (moved to phase 3b);
- 4f `examples/help`: build, bundle, install, check and stop an
  installation **with Help only**, then with all three apps. In three
  deliveries: 4f-1 each app's own pod template and its check (5.9); 4f-2
  an app without a database or login installs (Help alone); 4f-3 the three
  apps together and a lab run. `rewrite` and the header policy (5.3) wait
  for the first app that serves another app's help path: Help on its own
  hostname needs neither.
- 4g closing the gaps an outside review of the acceptance found (after run
  2026-10-10-run-53), kept small: a CI job installs Help **alone** in real
  rootless Podman, checks that no PostgreSQL or Keycloak runs, restarts it
  and checks again; and the lab's `check services` compares each serving
  unit's `Requires=` and `After=` on the host, as systemd loaded them, with
  the model. No large combination matrix: the operational acceptance stays
  as it is.
- 4h simplification before phase 5, from an outside review of the Python
  code at 2f250a7 (YELLOW: sound foundation, some friction). No new
  behaviour; the render baseline stays as it is but for the host record's
  version:
  1. One clear sequence for an app: read its YAML, render its pod template
     in one function, check it (`pod_contract`), then build the model. Today
     `platform_file._containers` renders the template while loading and
     `render.files` renders and checks it again; afterwards the rendered pod
     is made and read in one place.
  2. The values install passes between its steps (platform, environment,
     port, directories, target) in one small dataclass, instead of 7-value
     tuples and 11-parameter functions; nothing more general.
  3. The host's recorded platform (`platform.json`) carries its format
     version like `bundle.json`, so a record from an older installer is
     refused with a message that says so. Still no backward compatibility.
  4. From the 4f/4g understandability check: an app without a `ready` path
     is waited for until its containers (`App.containers`) run, not only
     until its unit started; and install refuses a `platform.yaml` that
     leaves out an app the host has installed (removal is 5.8, not built),
     naming the app, instead of leaving it running unmanaged.
  Not in 4h: `backend.setup_roles` (phase 5), and `pod_contract`, which
  stays a bounded contract check, widened only when an app needs it.
- 4i the image a bundle carries is the image that runs. Every image has
  the fixed tag `settings.IMAGE_TAG`, and an offline install kept an image
  already on the host under that reference: a new bundle with a fix under
  the same tag installed "successfully" and ran the old code. The bundle
  build records each image's ID (`images/image-ids.json`, under
  `SHA256SUMS`); an offline install, and a DR host loading from a bundle,
  loads an image whose ID on the host is another, and stops if the loaded
  image's ID is not the bundle's. This is not migration: the branch is
  version zero, and this is what its first fix needs.

**Phase 5: Database and login through the same model.**
Database and login are app fields since phase 4c-1; here come the setup task and
migration grants (5.5); pod template
validation (5.9). Todo and Notes move to `examples/`.
Accepted only with a **third reference app with a database and login** that
is neither Todo nor Notes (for example `examples/guestbook`): its own
source in its directory, its own endpoints and ready path, built, bundled,
installed and checked **without a change to platform code**, with a row of
its own carried through failover, backup and PITR in the lab. Todo and Notes
share assumptions (the backend's `setup_roles`, the ConfigMap template, the
`todo` realm); a third app is what shows whether phase 5 removed them.
The third app is written by someone without this branch's history (a fresh
agent or a person) from the documentation alone, not by the agent that built
the platform: otherwise it tests the contract, not whether it can be
understood. What they had to ask or guess is recorded and fixed.
From the 4h understandability check, phase 5 must also:
- make the platform create each app's roles and base rights from the model,
  so no app copies Todo's `setup_roles.py` with its role and secret names;
  until then the setup contract (a `backend` image running
  `python -m backend.setup_roles`) is written down beside the loader's rule;
- move table grants into the app's migrations and drop the revoke-then-grant
  that runs again after the app starts, so a migration's own grants stay;
  test a new table and a repeated install with the runtime role, which
  must be refused DDL;
- give the third app its own client ID, read by its frontend from its
  config rather than written in its JS, and check token audience and
  runtime CRUD; the shared `todo` realm is today's limit until phase 6.
Phase 5 starts with **5-1, the identity contract**, from an assessment of
replacing Keycloak with Duende IdentityServer (YELLOW: the apps' token
validation is plain OIDC, but the platform's Keycloak paths are spread over
some fifteen files and no page says what a provider must deliver). Small,
and before the third app, so its author finds the contract written down:
- `docs/IDENTITY.md`: what an identity provider must deliver (issuer and
  discovery, JWKS, `aud` per app, the claims a backend requires, client
  registration with redirect and origin, readiness, the `/auth/` route,
  its storage in the DR group), what is plain OIDC and what is Keycloak
  administration, and where the Keycloak-specific code is. No provider
  layer and no plugin system: a later switch changes those places.
- One place in Python (`keycloak.py`) for the issuer, the discovery path
  and the internal JWKS URL, used by the app ConfigMap, DR, backup and the
  lab instead of `/auth/realms/todo` written out; the failover login check
  takes `authorization_endpoint` from discovery instead of building it.
- `keycloakClient` becomes a neutral `login: {client: ...}` in app.yaml,
  and the token audience a backend checks comes from one function beside
  the client ID (with Keycloak's audience mapper they are equal today; with
  Duende the audience is an API resource's name).
- A backend requires `OIDC_AUDIENCE` like the issuer, instead of falling
  back to `todo-frontend`.
A second assessment of the same question (also YELLOW) adds:
- The client ID and the API audience must differ (`todo-frontend` and
  `todo-api`): today Keycloak puts the client ID in `aud`, which is also
  the audience the backend accepts, so an ID token issued to the frontend
  most likely passes the API's check. `login: {client, audience}` in
  app.yaml names both; Keycloak's audience mapper gives the audience, and a
  test shows an ID token is refused.
- Install corrects the audience mapper and PKCE of an existing client too,
  not only its redirect and origin: an install verifies the login contract
  it depends on.
- The token tests run without a database, and the adapter contract is
  tested for both apps.
- `docs/IDENTITY.md` also says which addresses the browser, the backend
  and the installer and DR use, what `Platform.has_identity` means (an app
  logs in, so the platform runs its own Keycloak; an external provider
  cannot be expressed yet), the access model (any logged-in user may write,
  no row ownership), and the adapter's behaviour at start, callback,
  expired session, failed refresh and logout.
5-1 is three deliveries: (a) the page and the addresses in one place, with
DR, backup, the lab and the failover check using them; (b) client and
audience apart, the mapper and PKCE corrected, `OIDC_AUDIENCE` required,
database-free token tests with an ID-token test; (c) each frontend reads
its client ID from its config instead of its JS.
Kept for an actual switch: the provider's own app and image, its client
registration, its signing keys across a failover (for Duende, the data
protection keys that encrypt them must replicate too), moving users (no
password hashes assumed portable), and a new frontend adapter.

**Phase 5b: An interrupted install, run again.** In CI, in real rootless
Podman, an install is stopped by a real failure at a few points (an image
archive missing, a database that does not become healthy, Keycloak not
answering) and then run again unchanged: data, credentials and the host
record are kept, and each message names the next step. No fault-injection
hooks in the code. After phase 5, since it changes role setup. One case is
known to fail today: `write_definitions` writes the files, and only then
are the changed pods restarted, so an install stopped in between leaves the
next run finding unchanged files (or images with the bundle's ID) while a
pod still runs the old ones. The fix belongs here, with its test: the
restarts still owed are recorded before the files change and cleared only
after the start.

**Phase 6: Keycloak semantics and multi-realm.** *Behaviour.*
Section 5.7: owned fields, idempotent setup, clients from defaults, every
`realms/todo` gone (5-1 gathers them in one place first), DR verification
per realm. Todo and Notes share a realm;
a test installation adds an app in its own realm.

**Phase 7: Lifecycle of apps.**
An app from a directory outside the repository; removal that keeps data and
an explicit purge (5.8); the DR group rule (5.6).

**Phase 8: Help under a path.**
Help also served at `/help/` on Todo and Notes, chosen in `platform.yaml`;
`strip` rewrite; the route tests in 5.3.

**Before phase 9: two investigations**, each a short written answer first
and work only for a gap it finds:
- Recovery on a new host when both are gone: whether the bundle, the
  backup, the secrets kept off-host and the operations package are enough
  to restore onto a host that was never a standby, then log in and read
  and write the restored data. The lab's PITR runs on existing hosts.
- A bounded security review, after phase 5's changes to secrets and roles:
  each secret from its source to the container, logs and backups; file
  modes; TLS verification; the ports actually published. The guide for a
  new app says that `pod_contract` checks supported templates and is not a
  sandbox for unknown apps.

**Phase 9: Documentation and acceptance.** *Behaviour.*
"Add an app" guide with a skeleton for each app shape; ARCHITECTURE,
LEARNING-GUIDE and DR docs for the platform; full acceptance run; final
understandability check by an agent without history, including adding a
fourth app by the guide alone.

## 8. Acceptance criteria

Existing security and DR tests stay. In addition:

- **Simplicity**: the understandability check passes after every phase, and
  the install control flow reads top to bottom in one module.
- An installation with only the static app and the components it needs.
- An app from a directory outside the repository.
- Two different platform models in the same process.
- Offline installation without Jinja2, PyYAML or network access.
- Running install again changes nothing.
- An interrupted Keycloak or install step can be run again and converges.
- Multi-realm: SSO within a shared realm, isolation across realms.
- Help on its own hostname and under a path.
- DR refuses to proceed when required data recovery is missing (an
  unclassified volume, a group mismatch).
- A new app added without changes to platform code.

## 9. Open questions

1. Section 5.5: may the platform own role creation, with table grants in
   the apps' migrations?
2. Section 5.6: is a standby rebuild the right way to change the DR group in
   version 1?
3. Section 5.8: is "remove keeps data, purge is explicit" the wanted
   default?

## 10. Phase log

**Phase 0, done.** Baseline of rendered output (`tests/test_render_baseline.py`,
57 files), name guard with 67 known platform files
(`tests/test_example_app_names.py`), recursive DR boundary test, AGENTS.md
updated. Understandability check: not needed, no installer or DR code
changed; the three new tests each explain in their docstring why they fail
and what to do.

**Phase 1, done: accepted with phase 2 in run 2026-10-10-run-50.**
- 1a: every shared `todo-` name is now `platform-` (proxy image, TLS
  secrets and volume, nginx paths, host directories, bundle and operations
  package, DR timers, CA wrapper). The lab's own names (VM names, firewall
  rule comments, `~/.config/todo-acceptance`) are kept until the lab is
  rebuilt.
- 1b: Keycloak has its own hostname, `auth.test` by default
  (`runtime.identityHostname`, `TARGET_IDENTITY_HOSTNAME`,
  `--target-identity-hostname`); every app has `TARGET_<APP>_HOSTNAME`, todo
  included. nginx serves Keycloak's hostname as its default server with only
  `/auth/`, so a request without a known `Host` reaches Keycloak, never an
  app. The proxy unit no longer reads `todo-config.yaml`, the Todo frontend
  discovers the issuer like Notes, a missing Keycloak client is copied from
  `apps.TEMPLATE_CLIENT` (looked up only when needed), and an install no
  longer requires the todo app. Bundle format version 5.
- Clients and the lab must map `auth.test` too (`/etc/hosts` lines in the
  guides and CI). Name guard: 52 known files left.
- Understandability check (fresh agent, at c22a980): **YELLOW**. The
  installer reads top to bottom without an internal framework; a small
  change (Keycloak's root redirect) was local and its test easy to find.
  The friction is scattered knowledge: adding an app means finding copied
  lists in Python, `.kube` templates, shell scripts, the lab tool, CI and
  tests. Its findings and where they go:
  1. Hand-kept lists in `wait-ready.sh`, `preflight.sh` and `acceptance.py`
     (phase 4e; proposed to move right after phase 3).
  2. The bundle's baseline fixtures were ignored by Git, so a clean checkout
     failed the baseline: fixed, and a test now refuses an ignored fixture.
  3. `install.py`/`workloads.py`: comparison and writing are mixed, and the
     shared ConfigMap needs the `configs_before` workaround (phase 2/3,
     where the model makes each workload's files explicit).
  4. Per-app `.kube` copies (phase 4d).
  5. Keycloak setup waits for app readiness; the app check (`/api/...`
     returns a list) is implicit (phase 4c).
  Also noted: setup roles and migrations assume Python modules (phase 5
  contract), checksum and shell preflight run only in `install.sh`, and
  the proxy entrypoint tests need a writable `/tmp`.

**Phase 2, done: accepted in run 2026-10-10-run-50 on `425f6b3`**
([record](history/ACCEPTANCE-425f6b3.md)). The lab found two things the
tests had not: the quarantine helper's own Python still imported a removed
name (its test faked `python3`; now a test runs it), and the preflight port
probe failed on TIME_WAIT right after an uninstall (`SO_REUSEADDR` now).
- `apps.Platform` (apps in start order, Keycloak's default hostname) answers
  the DR group, workloads and services; `apps.registry()` is the only list
  of apps in the code, used by build and dev mode, `replication-apps`, the
  image export and the lab tool (until phase 3b).
- Where a running tool gets its platform: a bundle or operations package
  from its `bundle.json` (format version 6); a host from its record
  `~/.config/platform/platform.json`, written before an install or a DR step
  changes anything; app-ops from its own package. `app_dr_host` takes
  `--project-root` before the command and reads the staged `bundle.json`;
  app-ops now stages it on every host it runs `app_dr_host` on.
- Every function that acts on an installation takes the platform; no
  module-level app lists and no defaults that bind one remain. Two
  platforms in one process are tested.
- Offline hostnames: `--target-hostname NAME=HOSTNAME` (NAME `identity` or an
  app of the bundle) replaces `--target-<app>-hostname`, so the CLI no longer
  needs the apps in advance; `install.sh` passes it on.
- Smaller changes on the way: a replication server certificate names only
  its own database container; `preflight_rebuild` checks the confirmations
  before it stages anything; a missing record or an unknown app is a clear
  error. The render baseline changes only in `bundle.json`.
- Understandability check (fresh agent, at 49e8804): **YELLOW**. The five
  installer paths to the platform were traceable (two to four file hops),
  and a small change (refusing an app named `identity`) was local, with its
  test easy to find. Adding a third app is still repository-wide knowledge:
  the installer follows the model, but readiness, preflight, acceptance, e2e
  and CI keep their own lists. Its findings and what was done:
  1. Build mode rendered through `render-kube-runtime.sh`, which rebuilt
     the platform from the registry by app names, so `install(platform=...)`
     was not what got rendered: fixed, an install renders the platform it
     installs (`render.render(..., platform)`); the script renders the
     registry's and no longer takes app names.
  2. Hand-kept lists in scripts, acceptance and CI: phase 3b, now naming
     every one it found.
  3. `down` treated a broken platform record as "nothing installed": fixed,
     only a missing record means that, a broken one is an error.
  4. Unused plumbing: the app units' `publish_address`/`service_port`
     variables are gone. `replication.reseed_check` keeps `project_root`,
     because it takes the same path arguments as `bootstrap_standby`.
  5. This plan said nothing was implemented and described the code before
     phase 2 as today's: fixed (status line, section 4).
  Also done: the app name `identity` is reserved (it names Keycloak in
  `--target-hostname`), the installer's CLI is no longer the "Todo
  installer", standby preflight says it stages files, and the installer
  README tells the two host records apart (`platform.json` before an install
  changes anything, `target-values.json` after it succeeded). Noted, not
  changed: setup and migration still assume Python entry points (phase 5
  contract), and the proxy entrypoint tests need a writable `/tmp`.
  Name guard: 51 known files left.

**Phase 3, done: accepted with phase 3b in run 2026-10-10-run-51.** Commits `048fa3d`
(the files and the loader) and `d74b768` (the callers; its subject says
"Phase 3b" by mistake: it is phase 3, and phase 3b is still to come).
- `platform.yaml` (the operator's: Keycloak's hostname, the public port and
  log level with a `local` and a `prod` environment that may change only
  those two, and the apps by directory with their hostnames and replication
  ports) and `examples/todo/app.yaml`, `examples/notes/app.yaml` (the
  developer's: name, OAuth client, REST collection) hold today's values. The
  app sources stay at the repository root until phase 5.
- `app_installer/platform_file.py` loads and checks them: unknown or missing
  fields, bad hostnames and ports (`publicPort` 1024-65535) and clashes
  between apps are errors that name the file and the field. PyYAML is
  imported only when a file is read, so offline hosts never need it.
- `apps.registry()`, `apps.IDENTITY_HOSTNAME`, `render.read_values`,
  `render.platform` and `deploy/environments/*/values.yaml` are gone. Build
  and dev mode, `render-kube-runtime.sh` (now `[ENVIRONMENT] [OUTPUT]`), the
  bundle and operations package builds, the image export and
  `replication-apps` read `platform.yaml`; the tests and the lab tools use
  `platform_file.checkout()`, this checkout's file. Rendered output unchanged
  (render baseline).
- Not in v1 of the files, because nothing uses it yet: the realm each app
  uses (all apps share the `todo` realm today) and `which app serves another
  app's help path`; they come with the phases that need them.
- Understandability check (fresh agent, at a9117f8): **YELLOW**. The loader
  needs no framework, its messages for a typo or a bad hostname say what to
  fix, and a small change (an experimental environment override) took one
  file and its test. But the files do not yet describe everything an app
  needs, and adding one is still repository-wide knowledge. Its findings and
  what was done:
  1. `publicPort` and `--service-port` were two sources for one port: a
     build could render URLs for one port and publish another. Fixed: the
     port is `platform.yaml`'s (build mode) or the bundle's (offline);
     `--service-port` may only repeat it, and a test publishes 9443 from
     `platform.yaml` alone.
  2. Hand-kept lists in scripts, acceptance and CI: phase 3b.
  3. `install.prepare` read `platform.yaml` a second time for the
     environment: fixed, `check` loads (Platform, Environment) once
     (`install.build_settings`) and passes both on.
  4. Clashes between apps were reported with Python field names and no apps:
     fixed, `platform_file` names the YAML field and both apps' `app.yaml`
     (and Keycloak's port 5434); an environment that is not a mapping (for
     example `false`) is an error, not "no changes".
  5. `apiCollection` was described as nginx routing; it is what the DR tools
     read to check the app (nginx forwards all of `/api/`): the comments say
     so. Section 5.1 now says which fields exist today.
  Also: the bundle README no longer calls itself the Todo bundle (name guard
  50), test names no longer say "registry", and `checkout()` says it reads
  once per process. Noted, not changed: `apps[].path` locates only
  `app.yaml` (sources until phase 5), the setup still assumes Python entry
  points (phase 5), and the proxy entrypoint tests need a writable `/tmp`.

**Phase 3b, done: accepted in run 2026-10-10-run-51 on `8079d06`**
([record](history/ACCEPTANCE-8079d06.md)); understandability check pending.
- `wait-ready.sh` keeps no list: its callers pass the pods, containers and
  hostnames (`apps.Platform.ready(role)`), failover from the promoted host's
  platform, the lab tool from `platform.yaml`.
- `python3 -m app_installer platform PART` prints what a shell script needs
  (`hostnames`, `hostname NAME`, `public-port`, `host-ports`) from
  `platform.yaml`, or with `--bundle-dir` from `bundle.json` with the
  standard library alone. `preflight.sh` takes the bundle's host ports
  (`apps.Platform.host_ports`); `run-e2e.sh` and the clean-install workflow
  take the hostnames and the port of `platform.yaml`'s local environment.
- The lab tools (`acceptance.py`, `acceptance_preflight.py`) take the
  databases, hostnames, public port, the replication port range of the
  firewall rules and the containers from `platform.yaml`. What stays named
  is the example apps' own checks: browser flows, markers, write probes
  and PITR rows test Todo and Notes themselves.
- Still hand-kept: the nginx smoke test's hostnames
  (`deploy/scripts/dev/smoke-proxy.sh`), the per-app `.kube` templates
  (phase 4d) and the example apps' CI jobs. The firewall rules allow the
  range from the lowest to the highest replication port, as app-ops'
  standby rule does. Name guard: 48 known files left.

**Phase 4a, done: accepted in run 2026-10-10-run-52 on `41a2af0`** ([record](history/ACCEPTANCE-41a2af0.md)).
- Each `app.yaml` declares the images the app builds (`images: {NAME:
  {context, containerfile}}`, section 5.2): `context` relative to the app's
  directory, possibly outside it (a source tree elsewhere), kept relative to
  the project root; `containerfile` relative to the context and inside it.
  The image is still `localhost/<app>-<NAME>:<tag>`. `apps.AppImage` holds
  one; `images.image_list` builds exactly the declared images, from their
  context (`podman build --file CONTEXT/CONTAINERFILE CONTEXT`), and
  `uninstall` removes exactly them. An app asked for an image it does not
  declare is an error.
- Todo and Notes declare `backend` and `frontend` with the repository root as
  context, so they build exactly as before. `bundle.json` carries the images
  (format version 7); the render baseline changes only there.
- Not yet: prebuilt images (`reference`, pulled): no app needs one, so they
  come with the first that does. Which shared images a bundle carries
  (Keycloak and PostgreSQL only when needed) is phase 4c.
- Understandability check of phases 3b and 4a (fresh agent, at 200941c):
  **YELLOW**. The scripts' inputs and an image's way from `app.yaml` to an
  offline host were traceable in two or three file hops, the image errors
  name the file and field, and a small change (a `platform databases`
  part) was local. Adding an app is still repository-wide knowledge: the
  per-app `.kube` templates, the example apps' acceptance checks and
  default-installation test assertions. Its findings and what was done:
  1. Per-app `.kube` template copies: phase 4d generates the units.
  2. An app image named `proxy` was taken for the shared nginx image (its
     label check): fixed, the check goes by the proxy's reference.
  3. Failover's HTTPS and login-page checks, and `deploy-promoted`, used
     the default port 8443 instead of the bundle's: fixed, app-ops takes the
     port from the operations package's `bundle.json` (`steps.public_port`),
     and a test runs a failover on 9443.
  4. Tests of the operator's `platform.yaml` and of a fixed sample platform
     are mixed (`runtime_fixture.py` and others): noted, to separate when a
     third app makes it worth it.
  5. The plan named format 6 and image-ID comparison as current: fixed,
     section 4 says format 7 and that image IDs are not compared yet;
     section 5.2 says prebuilt images come later.
  Also: an `app.yaml` without the `backend` and `frontend` images the shared
  pod template runs is refused when it is read, naming the file
  (`platform_file.TEMPLATE_IMAGES`), not later while rendering; `cli.py` says
  `platform` prints plain text; README and test wording no longer say
  "registry"; `reseed_check` says why it takes `project_root`. Noted: the lab
  tool reads `platform.yaml` at import (a lab tool, not platform code), and
  `Platform.ready` still names the template's backend and frontend
  containers (phase 5, the apps' own pod templates).

**Phase 4b, done: accepted in run 2026-10-10-run-52 on `41a2af0`** ([record](history/ACCEPTANCE-41a2af0.md)).
- Each `app.yaml` declares its `endpoints` (a name and the `port` in its pod
  nginx sends requests to) and its `routes` (`path`, `to` an endpoint, and
  `exact` for a whole-path match), section 5.3. `apps.Endpoint` and
  `apps.Route` hold them; `Route.location` is the nginx location.
- nginx is rendered from them: one upstream per endpoint
  (`<app>_<endpoint>` at the app's pod), one location per route in the
  order `app.yaml` lists them, after the platform's own `/auth/`. The
  loader refuses an unknown endpoint, a prefix path without its final
  slash, a path under `/auth`, a path with characters that could change
  nginx.conf, and the same location twice.
- Todo and Notes declare today's routes (`/api/`, exact `/health` and
  `/ready`, `/`), so every rendered nginx.conf is byte for byte as before;
  `bundle.json` carries the endpoints and routes (format version 8), the
  only change in the render baseline.
- Not yet: `rewrite` and the header policy (phase 4f, for Help), and an
  endpoint's container (with pod templates, 5.9).

**Phase 4c-1, done: accepted in run 2026-10-10-run-52 on `41a2af0`** ([record](history/ACCEPTANCE-41a2af0.md)).
Phase 4c is split: 4c-1 makes PostgreSQL and Keycloak run only when an app
needs them; 4c-2 brings readiness and checks into the model (5.4). The
start order (`requires`) comes with the generated units in 4d.
- An `app.yaml` says `database: true` when the app needs its own
  PostgreSQL, and names a `keycloakClient` when its users log in; both are
  optional (no database, no login). `platform.yaml` gives a
  `replicationPort` exactly to the apps with a database. `App.has_database`
  and `App.has_login` hold them; `App.database` of an app without one is an
  error that names the app.
- `Platform.database_apps`, `login_apps` and `has_identity` (some app has
  login) are what the consumers ask: the DR group, workloads, services and
  readiness; the Kube secrets and generated passwords; the shared images
  (PostgreSQL only with a database, Keycloak only with login); the rendered
  files and units (Keycloak, its database and `keycloak.kube` only with
  login, and `shared-proxy.kube` requires `keycloak.service` only then);
  nginx (Keycloak's upstream, `/auth/` on an app's hostname only for an app
  with login, and the CSP's identity origin only when Keycloak runs);
  install and development mode (no role setup without a database, no
  Keycloak configuration without login); DR (the promoted host's Keycloak,
  issuer check and clients, the backup restart's issuer wait, failover's
  login page check per login app). app-ops refuses a platform without any
  database: it has no DR group.
- Todo and Notes say `database: true` and keep their clients, so every
  rendered file is as before; `bundle.json` carries `has_database` (format
  version 9), the only change in the render baseline. Tests use synthetic
  platforms without a database, without login, and with neither.
- Not yet: an app without a database or login cannot be rendered, because
  the one shared app pod template uses both; the render says so. Its own
  pod template comes with Help (4f). `identityHostname` is still required
  when no app has login (it names nginx's default server). Removing
  Keycloak's units from a host whose platform no longer needs it is app
  removal (5.8).

**Phase 4c-2, done: accepted in run 2026-10-10-run-52 on `41a2af0`** ([record](history/ACCEPTANCE-41a2af0.md)).
- An `app.yaml` may declare `ready`, the HTTP path on its hostname that
  answers 200 once the app can serve, and `checks`, GET requests with the
  status each must answer (`apps.Check`). The loader refuses a path no route
  of the app matches and a status outside 100-599. `apiCollection` is gone:
  Todo and Notes declare `ready: /ready` and check `/health` and
  `/api/todos` or `/api/notes` for 200.
- `checks.py` asks nginx on 127.0.0.1 with the app's hostname: `wait_ready`
  polls the ready path, `run` the checks once, `verify` both for every app,
  and `ready_urls` gives `wait-ready.sh` each `HOSTNAME/PATH` (it no longer
  assumes `/ready`).
- Install (server and development) and `deploy-promoted` run
  `checks.verify` after Keycloak is set up; `keycloak.configure` no longer
  waits for the apps, so identity setup never waits for an app.
  `promoted.require_application` is gone. Publishing the primaries, the
  backup's restart and failover's services check wait for each app's own
  ready path.
- The only change in the render baseline is `bundle.json`: `ready` and
  `checks` instead of `api_collection` (format version 10).
- Not yet: a check compares the status only, so the old "the public read
  returns a list" is now "answers 200". Setup tasks (5.4 item 3) come with
  phase 5, start order with 4d.
- Understandability check of phases 4b and 4c (fresh agent, at bfdff46):
  **YELLOW**. Install's five steps, `checks.py` and the model's
  `database_apps`, `login_apps` and `has_identity` were easy to follow, and
  the YAML errors name the file and field. The friction is the gap between
  what the model can describe and what today's pod and unit templates can
  install. Its findings and what was done:
  1. An app the shared pod template cannot run (no database or login), or
     without its own unit templates, was refused only while rendering,
     after the host had recorded the platform: fixed,
     `render.require_supported` names what is missing, and `install.check`
     and `bundle.build` call it before anything is written (tested: no command,
     record unchanged).
  2. Each app's `.kube` units are copied templates: phase 4d generates them.
  3. An app without `ready` was said to be "ready when it runs", which
     `checks.py` does not check: fixed in the docstrings, which now say
     nothing waits for it beyond its started unit (`wait-ready.sh` checks
     the containers). A container check per app comes with pod templates
     (5.9).
  4. A check followed redirects, so a 302 to a login page counted as 200:
     fixed, `checks.status` does not follow redirects (tested with a real
     302).
  5. With a `/` route, `ready` or a check could name `/auth/...`, which
     nginx sends to Keycloak: fixed, both are refused like a route under
     `/auth`.
  Also: `finish` no longer says a check "may need login" (a check sends no
  token). Noted: a check compares only the status, so it cannot prove a
  path is the app's own (a `/` route matches any path); and a host's
  recorded platform has no version of its own, which matters once a field
  is added after an installation (no backward compatibility so far, as
  decided).

**Phase 4d, done: accepted in run 2026-10-10-run-53 on `a81cdca`** ([record](history/ACCEPTANCE-a81cdca.md)).
- One unit template per kind of workload: `app.kube.j2` and
  `postgres.kube.j2` replace the five copies (`todo-app`, `notes-app`,
  `todo-postgres`, `notes-postgres`, `keycloak-postgres`); `keycloak.kube.j2`
  and `shared-proxy.kube.j2` stay. Adding an app adds no file in
  `deploy/quadlet`.
- `apps.Workload` gains `template` and `requires`; `apps.database_workload`,
  `apps.app_workload`, `apps.KEYCLOAK_WORKLOAD` and
  `Platform.proxy_workload` make them, and `Platform.workloads()` lists them
  in start order. Each template gets its workload (`workloads.*_variables`)
  and renders `Yaml=`, `ConfigMap=`, `Requires=` and `After=` from it; an app
  without a database or login requires neither, and nginx needs no Keycloak
  without login.
- Every rendered unit is byte for byte as before, but for Keycloak's
  description ("Shared Keycloak", no longer "Todo Keycloak"), the only
  change in the render baseline. The name guard shrinks by five files.
- `render.require_supported` no longer looks for an app's own unit
  templates (there are none); it checks only that the shared pod template
  can run the app.
- Understandability check of phase 4d (fresh agent, at 3383c09): **YELLOW**.
  One template per kind of workload made a global unit change local, the
  workload functions state the dependencies directly, and start, the DR
  tier and stop all take the one list. Its findings and what was done:
  1. Build mode took the template from the workload, the bundle named it
     again in `bundle.py`: fixed, both render through
     `workloads.unit_file`, which takes the template from the workload.
  2. That each workload requires only earlier ones was checked only by
     tests: fixed, `Platform` refuses itself otherwise (the list stays
     explicit, with no dependency sort).
  3. The baseline's build output came from the bundle's rendering, so it
     did not prove that a build-mode install writes the same unit: fixed,
     the workload install test compares each unit `_install` writes with
     the bundle's.
  4. A missing unit template was found only when that workload was
     installed, after others had changed: fixed, `render.require_supported`
     also checks every workload's template, before `install.check` and
     `bundle.build` write anything. Each workload is still written in turn,
     not all at once.
  5. `keycloak.kube.j2` and `shared-proxy.kube.j2` named their Kube YAML
     literally: fixed, `Yaml=` comes from the workload in all four.
  Noted: per-app unit settings (such as a longer start timeout for one
  app) would be a small explicit field when an app needs one.

**Phase 4f-1, done: accepted in run 2026-10-10-run-53 on `a81cdca`** ([record](history/ACCEPTANCE-a81cdca.md)).
- Each app brings its own pod template, `pod.yaml.j2` beside its
  `app.yaml` (`App.pod_template`); `deploy/manifests/app.yaml.j2` is gone,
  and Todo and Notes each have a copy (some duplication is fine; Help's
  will differ).
- `manifests.render_app` renders it with `app` and `images`, and
  `pod_contract.check` holds the result to the platform's rules (section
  5.9) in `render.files`, so a bundle and a build install both check it; a
  build install now renders before the host records the platform.
- The loader requires the pod template, and a `backend` image only for an
  app with a database (its `backend.setup_roles`), no longer `backend` and
  `frontend` for every app.
- Every rendered Kube YAML file is byte for byte as before; `bundle.json`
  carries `pod_template` (format version 11), the only change in the render
  baseline.

**Phase 4f-2, done: accepted in run 2026-10-10-run-53 on `a81cdca`** ([record](history/ACCEPTANCE-a81cdca.md)).
- `examples/help` is the static example app: one unprivileged nginx serving
  plain HTML (`html/`), its own `Containerfile` and `pod.yaml.j2`, one
  endpoint and the route `/`, `ready: /` and a check of `/`. No database,
  no login, so no secret and no setup.
- An app without a database or login now installs: its ConfigMap holds only
  `LOG_LEVEL` (`app-config.yaml.j2` adds the database's and OIDC settings
  only for an app with them), it gets no Kube secrets, and a change of any
  of its images restarts it (not only `backend` and `frontend`). The early
  refusal of such apps is gone.
- Tested with a platform of Help alone: its bundle is built, and the
  offline install runs only `help-app` and `shared-proxy`, no PostgreSQL,
  no Keycloak and no role setup, creates only nginx's TLS secrets, and
  checks Help's `/` through nginx. Todo and Notes render as before; the
  render baseline is unchanged.
- Not yet (4f-3): Help in this checkout's `platform.yaml` beside Todo and
  Notes, its image in CI and the bundle, `rewrite` and the header policy,
  and a lab run.

**Phase 4f-3, done: accepted in run 2026-10-10-run-53 on `a81cdca`** ([record](history/ACCEPTANCE-a81cdca.md)).
- This checkout's `platform.yaml` runs Help beside Todo and Notes, on
  `help.test`: eight pods on a single host. The DR group stays todo, notes
  and keycloak, since Help has no database; a database-only standby runs
  the same three PostgreSQL pods as before.
- Every rendered Todo and Notes file is unchanged. The render baseline
  gains Help's Kube YAML, ConfigMap and unit, nginx gains Help's server
  block and certificate name, and `bundle.json` the app; the bundle carries
  8 image archives.
- The lab guide's client lines (C9.4) take the names for `/etc/hosts` from
  `app_installer platform hostnames` instead of writing them out, so they
  follow `platform.yaml`; `docs/ACCEPTANCE.md` lists `help-app.service` and
  checks Help's `/`. The install, failover and `wait-ready.sh` already check
  Help through its own `ready` and `checks`.
- Tests that wrote the two-app checkout out literally now describe three
  apps; the services and units of the standby and DR paths are unchanged.
- Adding Help found a bug: `Platform.ready("app")` named each app's
  `<app>-backend` and `<app>-frontend`, so `wait-ready.sh` and failover's
  services step would have waited for Help containers that do not exist.
  Fixed: the loader renders each app's pod template and records its
  long-running containers (`App.containers`, in `bundle.json`, format
  version 12), and `pod_contract` checks the rendered pod runs exactly
  those. The app unit's description is now "<App> app" instead of
  "frontend and backend" (the render baseline's other change).
- Not built, and not needed by Help on its own hostname: `rewrite` and the
  header policy (5.3), which come with the first app that serves another
  app's help path.


**Phase 4g, done: accepted in run 2026-10-11-run-54 on `27e2f9a`** ([record](history/ACCEPTANCE-27e2f9a.md)).
After run 2026-10-10-run-53 an outside review asked whether the acceptance
covers what it should. For running and recovering today's installation it
does; for the platform's generality it does not yet. Three gaps, kept
small:
- A CI job (`help-alone`) runs a platform of Help alone in real rootless
  Podman: it starts it, checks that no PostgreSQL or Keycloak container and
  no database or Keycloak secret exists and that Help answers through
  nginx, takes it down and starts it again, and checks the same.
- The lab's `check services ... app` also compares each serving unit's
  `Requires=` and `After=`, as systemd loaded them on the host, with the
  model's `requires` (`acceptance.unit_dependencies`), so a reboot proves
  the generated dependencies and not only the installer's start loop.
- A third reference app with a database and login, neither Todo nor Notes,
  is phase 5's acceptance criterion (see phase 5).
Failure cases of the new contracts (a ready path that never answers, a
check with the wrong status, a dependency on a later workload, a missing
template) stay in the fast tests, not in new VM steps.
- Understandability check of phases 4f and 4g (fresh agent, at be896a5):
  **YELLOW**. A static app with two containers fits without a Python
  special case or a registration framework, contract errors come before
  any intended host change, and database and login read as plain
  conditions. Friction stays between loading, rendering and checking, and
  some checks did not hold the same contract. Its findings and what was
  done:
  1. The lab's dependency check took only the units that require
     something, and only looked for what was missing: fixed, it takes every
     unit and requires exactly the model's dependencies among the
     platform's own services (systemd's own additions may stay), so an
     extra dependency, also on Help, fails it.
  2. `wait-ready.sh` took any non-error answer, a 3xx too, where
     `checks.wait_ready` asks for 200: fixed, it asks for exactly 200 and
     names the status it got.
  3. `pod_contract` checked secret volumes and `envFrom` ConfigMaps, but
     not `envFrom` secrets or `env` keys from a ConfigMap or secret: fixed,
     every such reference must be the app's own.
  4. Loading renders a pod template that rendering renders and checks
     again: phase 4h, item 1.
  5. An app without `ready` gets no container check in `checks.py`: phase
     4h, item 4.
  6. A `platform.yaml` without an installed app replaces the host's record
     and leaves that app running: phase 4h, item 4.
  7. Generic tests lean on the example apps: noted; phase 5's third app
     makes small explicit test platforms worth it where a test checks
     generic behaviour.
  Noted: Help gets a ConfigMap it does not read, named `help-backend-config`;
  and `/auth/...` on Help's hostname goes to Help, which has no login. Both
  are harmless; the ConfigMap's name changes when the app config does
  (phase 5).

**Phase 4h, done: accepted in run 2026-10-11-run-54 on `27e2f9a`** ([record](history/ACCEPTANCE-27e2f9a.md)).
Simplification before phase 5; nothing an operator sees changes but two
refusals.
1. One load sequence: `pod_contract.rendered(project_root, app)` renders
   an app's pod template, reads it as YAML and checks it, and returns the
   content and the pod. The loader takes `App.containers` from it and
   `render.files` writes its content; nothing else renders a pod
   template. It runs twice per app, at load and at render, on the same
   input (tested: what is written is what was checked); the second run
   also holds the pod to `App.containers`. A broken pod template is refused
   already when `platform.yaml` is loaded.
2. `install.check` returns a `Plan` (what to install, where and how) and
   `install.prepare` a `Prepared` (what it rendered, the image changes,
   hostnames, whether nginx's TLS secret changed); `write_definitions` and
   `finish` take those two instead of 11 and 5 positional values.
3. The host record is `{"format_version": 12, "platform": ...}`
   (`target_render.platform_record`), with `bundle.json`'s version, since
   it carries the same platform data. A record of another version is
   refused with a message to uninstall with the installer that installed
   it; no backward compatibility, so a host installed before 4h must be
   uninstalled with that installer first.
4. An app without `ready` is waited for until each of `App.containers`
   runs (`checks.running`, `podman container inspect`); and
   `install.check` refuses a platform that leaves out an app the host's
   record holds, naming it, before anything changes
   (`install.require_installed_apps`).
The render baseline is unchanged: the record is not rendered output.

**Phase 4i, done: accepted in run 2026-10-11-run-54 on `27e2f9a`** ([record](history/ACCEPTANCE-27e2f9a.md)).
From an outside review of what to examine next, with the branch taken as
version zero (no migration from earlier states is owed):
1. An offline install ran a host's older image under the same tag: the
   bundle now records each image's ID (`images/image-ids.json`), an offline
   install and a DR host load an image whose ID on the host is another, and
   stop if the loaded image's ID is not the bundle's. Bundle format version
   13 (render baseline: only that number). This gives what unique tags per
   delivery would, without tags to keep apart; the lab proves the IDs
   survive `podman save` and `podman load`, which no CI job does.
2. A record in another format told the operator to uninstall with the old
   installer, but a plain uninstall keeps the record, so the next install
   was refused again: the message now gives the whole way over (a plain
   uninstall keeps databases and secrets, then remove the record, then
   install), tested with the data kept.
3. The guard compared app names only, so turning an app's database off, or
   login off for every app, left PostgreSQL or Keycloak running unmanaged:
   it now compares the services, and names those a platform would leave.
4. A pod template with a field of the wrong kind (`spec: wrong`, a numeric
   container name) gave a Python traceback: `pod_contract` first checks
   the kind of every field its rules read and names it by its path
   (`spec.containers[0].name must be a string`). No schema framework.
5. An install stopped between writing files and restarting pods: phase 5b,
   with the fix it needs.

**Phase 5-1a, done: accepted in run 2026-10-11-run-56 on `502ba89`** ([record](history/ACCEPTANCE-502ba89.md)).
The identity contract, first part. `docs/IDENTITY.md` says what a login
provider must deliver (issuer, discovery, JWKS, the token a backend
accepts, one client per app, the `/auth/` route, readiness, its state in the
DR group), which part is plain OIDC and which Keycloak's administration,
which address the browser, a backend and the installer and DR use, what
`has_login` and `has_identity` mean, the access model, the frontend
adapter's behaviour, and the order of work for a switch, with the tests
that guard login. Its limits are stated with the phase that lifts them.
`keycloak.py` holds every Keycloak address (`REALM_PATH`, `DISCOVERY`,
`issuer()`, `JWKS_URL`, `ADMIN_TOKEN`, `ADMIN_REALM`); the app ConfigMap,
the promoted host's issuer check, backup, the lab and the CI login check use
them, and failover's login check takes the authorization endpoint from
discovery. `/auth/realms/todo` is now written out only in the frontends'
adapters (5-1c). Four platform files leave the example-name guard. Render
baseline: `OIDC_JWKS_URL` quoted like the other values.

**Phase 5-1b, done: accepted in run 2026-10-11-run-56 on `502ba89`** ([record](history/ACCEPTANCE-502ba89.md)).
Client and audience apart. `app.yaml`'s `keycloakClient` became
`login: {client, audience}` (`App.login_client`, `App.login_audience`),
and `OIDC_AUDIENCE` is the audience (`todo-api`, `notes-api`), not the
client: Keycloak's ID token names the client, so an ID token no longer
passes a backend. The loader and `apps.Platform` refuse an audience equal
to its own client or to any app's client, and two apps sharing either.
Keycloak's clients come from the platform's defaults (`keycloak.new_client`;
`apps.TEMPLATE_CLIENT` and the realm import's client are gone), and an
existing client gets its owned fields corrected (public, code flow only,
PKCE S256, redirect URI, web origin) and exactly one audience mapper, the
app's API audience in access tokens only; any other audience mapper is
removed, since it could let one app's token pass another's backend. Each
backend requires `OIDC_AUDIENCE`; its token tests run without a database
(`unit_tests`) and refuse an ID token, another app's token and a missing
claim; the browser test takes the real ID tokens from Keycloak's token
endpoint and sends them to the API, which must answer 401. Bundle format
version 14. Render baseline: `login_client` and `login_audience` in
`bundle.json` in place of `keycloak_client`, `OIDC_AUDIENCE` the API
audience, and the format version. One more file leaves the example-name
guard.

**Phase 5-1c, done: accepted in run 2026-10-11-run-56 on `502ba89`** ([record](history/ACCEPTANCE-502ba89.md)).
Each frontend reads its login settings instead of naming them. The platform
renders `keycloak.frontend_login(app.login_client)` (discovery path, realm,
client ID) into the app's ConfigMap as the key `FRONTEND_LOGIN`; Todo's
and Notes' pod templates mount that key alone into the frontend as
`/config/login.json` (a ConfigMap volume with `items`, which `pod_contract`
already allowed as the app's own), and the frontends' nginx answers
`/config/` with a file or 404. The adapters read it and name no realm,
client or `/auth` path; local development without containers serves the
same file from `FRONTEND_LOGIN`. The adapter test serves a client ID found
nowhere in the code; the browser test checks each app's `login.json` on
the real stack and that the ConfigMap's other keys (its database settings)
answer 404. `/auth/realms/todo` and the client IDs are now written only in
`keycloak.py` and the app.yaml files. Render baseline: the ConfigMap's
`FRONTEND_LOGIN` and the frontend's mount in each app's pod.
Lab run 2026-10-11-run-55 stopped at `03-1u5-nothing-left`: kube play makes a
named volume of a ConfigMap a pod mounts, and uninstall removed only nginx's
(`shared-nginx-config`), so `todo-backend-config` and `notes-backend-config`
were left. `uninstall.config_volumes(platform)` now names each app's
ConfigMap volume too (absent ones are skipped).
- Understandability check of phase 5-1 (fresh agent, at eb3e839):
  **YELLOW**. The login chain can be followed without the history, and the
  client/audience split and its reason are well explained. Its findings
  and what was done:
  1. `correct_audience` took a mapper with the right custom audience but
     also an `included.client.audience` as right, and Keycloak prefers the
     client audience: fixed (`keycloak.audience_mapper_is` requires the
     client audience empty), with a test.
  2. "A wrong client is put right" said more than the code does: a table in
     `docs/IDENTITY.md` says which fields are made and which corrected
     (`enabled` stays the operator's), and CI now breaks Todo's client in
     the real Keycloak and checks the setup puts it right
     (`deploy/scripts/dev/check_client_repair.py`), before the browser
     tests log in with it.
  3. The way from file to file had to be found: `docs/IDENTITY.md` lists one
     login file by file.
  4. Two sentences were wrong (the backend's audience comes from
     `login.audience`; the installer reaches Keycloak as nginx's default
     server, without a Host header): corrected.
  5. "Two pod-template lines" was too short where a mistake is serious: a
     checklist for a new app with login, saying that `login:` protects
     nothing and the backend enforces access itself.
  6. What the DR checks prove: issuer, login page, real login and API, each
     named, with the login page's limits.

**Phase 5-2, done: accepted in run 2026-10-11-run-57 on `aaf89f8`** ([record](history/ACCEPTANCE-aaf89f8.md)).
Database roles from the platform, table rights from the migrations (5.5).
`app_installer.database_roles` makes `<app>_migrator` and `<app>_app` with
their passwords, `CONNECT` for those two only, the migrator as owner of the
schema and its objects, and `USAGE` on the schema for the runtime role; it
runs through the database's own `psql` (`podman exec`, as backup and DR run
SQL), the passwords on stdin, once per install after the database is healthy
and before the app starts, in server and development mode. It grants and
revokes no table rights: Todo's and Notes' first migrations grant
`<app>_app` theirs, so the revoke-then-grant that ran again after the app
started is gone, and a migration's own grants survive every install. The
backends' `setup_roles.py` are removed, and so is the loader's rule that an
app with a database declares a `backend` image. The CI backend job makes
the roles with the platform's SQL (`deploy/scripts/dev/apply_database_roles.py`),
migrates, makes them again, and runs the role tests as the runtime role
(CRUD allowed, DDL and the migration table refused).
- Understandability check of phase 4h (fresh agent, at 2cf33d3):
  **YELLOW**. Install's steps read in order, and `Plan` and `Prepared` are
  of a reasonable size with a clear job; the host record and the guard
  against leaving out an app follow 5.8's interim contract. Its findings
  and what was done:
  1. The pod template is rendered in one function but twice, at load and
     at render, so "once" overstated it: the wording is corrected and a
     test shows the file written is the pod checked. Keeping the loader's
     bytes for rendering would mean a second result from `load` for every
     caller and a platform from a bundle or a test that never loaded; one
     cheap, deterministic render more is the simpler price.
  2. `Plan` and `Prepared` used `Any` and bare `dict`: now concrete types,
     and `Prepared.rendered` is `rendered_directory`, as it holds a path.
  3. "Add it back to platform.yaml" does not help an offline host: the
     message now also says to build and deliver a bundle that includes
     the app, and that the record holds every app an install began.
  4. `checks.wait_ready` asked only whether a container runs, where
     `wait-ready.sh` also asks that it is healthy if it has a health check:
     both now ask Podman the same question (`checks.STATE`, tested against
     the script), and `App`'s docstring says what is waited for.
  5. to 7. A third app with a database would still copy Todo's
     `setup_roles`, its role and secret names; the revoke-then-grant after
     the app starts can remove a migration's own grants; and a third app
     with login inherits the `todo` realm and a client ID written in its
     JS: all phase 5, now written into its scope.

