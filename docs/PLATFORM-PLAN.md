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
are optional fields (`app.database`, `app.login`); code says
`if app.database:`. A shared interface is introduced only when two or more
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

Where it stands after phase 2: `apps.Platform` has two fields, the apps in
start order and Keycloak's default hostname, and derives the rest (the DR
group, workloads, services). `apps.registry()` builds it in code;
`bundle.json` (format version 6) carries it, and a host records it in
`~/.config/platform/platform.json`. Loading it from `platform.yaml` and
`app.yaml` is phase 3; the richer tree below grows with phases 3 to 5.

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
- **Versions**: `bundle.json` keeps `format_version` (6 since phase 2) and adds the
  image IDs of every image it carries. The operations package records the
  bundle format versions it supports and refuses others. The installer
  compares installed image IDs, not tags, with the bundle's.

## 5. Contracts

Each contract is short, documented next to the code that enforces it, and
tested.

### 5.1 Configuration files

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
  jobs use declared images only.
- The model decides exactly which images the bundle carries. A static-only
  installation carries nginx, Keycloak only if some app needs login, and
  PostgreSQL only if some app needs a database.

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

Identity setup waits only for Keycloak, never for the apps; app checks run
after identity setup. This removes the possible cycle where identity setup
waits for an app that waits for identity.

### 5.5 Database roles

The two `setup_roles.py` files are identical apart from names and two
table grants. Proposal: the **platform** creates the three roles (bootstrap
owner, migrator, runtime), their secrets, `CONNECT` and schema usage, in a
setup task using the PostgreSQL image; the **app's migrations** grant the
runtime role its table rights (the migrator owns the tables). Least
privilege stays per table, and the app no longer ships role code.

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

### 5.8 Adding, changing and removing apps

- Adding: a new directory and entry; install renders, installs and starts
  it. If it has a database, see the DR group rule (5.6).
- Removing an app from `platform.yaml` stops and removes its units and
  YAML, and **keeps** its database volume, secrets and Keycloak client. A
  separate, explicit command purges them.

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
  PostgreSQL only when needed;
- 4d generated `.kube` units and `kube play` order from the model;
- 4e (moved to phase 3b);
- 4f `examples/help`: build, bundle, install, check and stop an
  installation **with Help only**, then with all three apps.

**Phase 5: Database and login through the same model.**
`app.database` and `app.login` as optional fields; the setup task and
migration grants (5.5); checks replace `require_application`; pod template
validation (5.9). Todo and Notes move to `examples/`.

**Phase 6: Keycloak semantics and multi-realm.** *Behaviour.*
Section 5.7: owned fields, idempotent setup, clients from defaults, every
`realms/todo` gone, DR verification per realm. Todo and Notes share a realm;
a test installation adds an app in its own realm.

**Phase 7: Lifecycle of apps.**
An app from a directory outside the repository; removal that keeps data and
an explicit purge (5.8); the DR group rule (5.6).

**Phase 8: Help under a path.**
Help also served at `/help/` on Todo and Notes, chosen in `platform.yaml`;
`strip` rewrite; the route tests in 5.3.

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
