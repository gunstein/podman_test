# Platform plan: from the Todo demo to a reusable app platform

Status: **plan for review**. Nothing in it is implemented yet. The design it
carries out is [PLATFORM-DESIGN.md](PLATFORM-DESIGN.md); this document is the
order of work, what each step touches and how we know it is done. It is
written to be read on its own, including by a reviewer without the
repository, and ends with the questions we want challenged.

## 1. Where we are

The repository (branch `feature/podman-kube`) runs a demo on **rootless
Podman** with the Podman-supported subset of Kubernetes YAML as workload
format, no Kubernetes cluster:

- **Seven pods** on one user-defined network `app-network`: `todo-app` and
  `notes-app` (each a migration init container, a FastAPI backend and a plain
  HTML/JS frontend), `todo-postgres` and `notes-postgres`, `keycloak` with
  its own `keycloak-postgres`, and `shared-proxy` (nginx, TLS, routing by
  hostname).
- **Development**: `podman kube play/down`. **Production**: `.kube` Quadlet
  units under user systemd.
- **Installer** (`deploy/installer/app_installer`, Python): renders Jinja2
  templates (`deploy/manifests/*.yaml.j2`, `deploy/quadlet/*.kube.j2`) at
  build time. The **offline bundle** carries rendered YAML with
  `${TARGET_*}` placeholders, OCI image archives and `bundle.json`; the
  target fills them in with the Python standard library only.
- **DR** (`deploy/dr`): `app-ops` on a controller and `app_dr_host` on each
  host, over plain SSH. PostgreSQL streaming replication, fencing,
  promotion, backup, PITR and standby rebuild, always on the **whole group**
  of databases (todo, notes, keycloak). DR may import the installer, never
  the reverse (`tests/test_dr_boundary.py`).
- **Secrets** are Podman secrets, never plaintext YAML. SELinux and rootless
  storage, failure semantics and persistence are covered by tests and by a
  recorded acceptance run (`docs/ACCEPTANCE.md`).
- **Registry**: apps are listed in Python (`apps.py`: `APPS`,
  `IDENTITY_APP`, `REPLICATED_DATABASES`, and since S5 one ordered workload
  table `workloads()`). Much is already driven by it, but the platform is not
  generic: about 70 files of platform code (installer, DR, templates,
  scripts, proxy, Keycloak) still name `todo` or `notes`; the Keycloak realm
  `todo` is hard-coded in 17 places; shared resources carry a `todo-`
  prefix; quadlet units are written per app; the proxy template assumes every
  app is frontend + backend + database.

## 2. Where we are going

An open source platform where:

- **adding an app** = an app directory (`app.yaml`, `pod.yaml.j2`, source and
  Containerfiles) + one entry in `platform.yaml`, with **no platform code
  changes**;
- **adding a new kind of shared service or app** (not known yet) = one new
  *component* or *feature* class with its templates and tests, with **no
  changes** to the installer core, proxy template or DR code.

Decisions already taken by the owner:

1. No backward compatibility with existing installations or names.
2. One shared Keycloak, on its own hostname (`auth.<domain>`). Each app picks
   a realm; apps may share one realm (shared users, SSO) or have their own.
3. A login app: frontend + backend in one pod, PostgreSQL in its own pod.
4. Static apps exist (no login, backend or database), first for help text,
   which will be used in several ways (own hostname, under an app's path, ...).
5. More shared services and app kinds will come; which ones is not known.
6. Todo, Notes and Help stay in the repository as examples.
7. `todo` is never a general name. Proposed: prefix `platform` for shared
   resources, "app" for user workloads (pending confirmation).
8. Abstraction is acceptable, but a moderately experienced Python developer
   must be able to maintain and extend it.

Design summary (details in PLATFORM-DESIGN.md):

- **Ownership**: the platform owns everything DR, routing and identity depend
  on (databases, Keycloak, realms and clients, nginx, TLS, network,
  generated units, installer, bundle, DR). The app owns its pod template,
  source, migrations and `app.yaml`. An app declares a database; it never
  brings database YAML.
- **Two configuration files**: `platform.yaml` (operator: components,
  realms, apps with hostname, realm and replication port, environments) and
  `app.yaml` (app developer: images and features).
- **Components** (shared, one each: `proxy`, `identity`, later others) and
  **features** (what an app uses: `database`, `login`, `help`, later others)
  are plain Python classes answering the same questions: `workloads()`,
  `requires()`, `routes()`, `secrets()`, `template_values()`,
  `replicated_databases()`, `configure()`, plus a **data policy**
  (`replicated`, `local`, `none`). A fixed dictionary lists them; no plugins
  or dynamic imports. There are no app kinds in code, only skeleton
  directories.
- **Routing table**: nginx is rendered from `Route(hostname, path,
  upstream)` collected from components and features.
- Build mode reads YAML and writes the resolved model into `bundle.json`;
  offline targets still need only the standard library.

## 3. Rules for the work

- Every phase ends with all unit tests green and a working install from an
  offline bundle. Phases that change behaviour say so explicitly.
- Phase 0 pins today's rendered output; later phases diff against it and
  explain every difference.
- One commit per task, pushed per phase. Old code is deleted, not aliased.
- The safety boundaries are not weakened at any point: external secrets,
  rootless SELinux storage, failure semantics, persistence, fencing,
  promotion, backup, PITR and rebuild of the whole group.
- `tests/test_dr_boundary.py` keeps holding: DR imports the installer,
  never the reverse. Components and features live in the installer.
- A full acceptance run (`docs/ACCEPTANCE.md`) at the milestones in section 5.

## 4. Phases

Size: S = a day or less, M = a few days, L = a week or more, for one
developer with an AI assistant.

### Phase 0: Baseline (S)

- Snapshot the rendered Kube YAML, quadlet units and `bundle.json` for build
  mode and an offline bundle, as test fixtures to diff against.
- Guard test: platform code (installer, DR, templates, scripts, proxy,
  Keycloak image) must not contain `todo` or `notes`. It starts with an
  allow-list of today's ~70 files; each phase shrinks it; it is empty at the
  end.
- Update AGENTS.md: new goal and the abstraction rule (proposed text in
  PLATFORM-DESIGN.md section 11).

Done when: fixtures and guard test in place, AGENTS.md agreed.

### Phase 1: Names and identity hostname (M, changes behaviour)

- Replace the `todo-` prefix of shared resources with `platform-`: proxy
  image and archive, nginx TLS secrets and Kube secret, TLS volume, bundle
  and operations package names, `~/.config/todo` and `~/.local/state/todo`,
  the DR timers (`todo-backup`, `todo-dr-check`,
  `todo-replication-tls`).
- Give Keycloak its own hostname instead of `IDENTITY_APP`'s: proxy server
  block, TLS certificate names, OIDC issuer, `TARGET_*` placeholders,
  failover and promotion checks.
- Files: `apps.py`, `tls*.py`, `uninstall.py`, `bundle.py`,
  `target_render.py`, `deploy/dr/systemd/*`, `deploy/dr/app_ops/failover.py`,
  `deploy/dr/app_dr_host/promoted.py`, `shared-proxy.yaml.j2`,
  `deploy/offline/*`, `deploy/scripts/*`, docs.

Done when: install and DR work with the new names; no `todo-` shared names
remain; guard allow-list shrunk.

### Phase 2: Configuration from YAML (M)

- `platform.yaml` and `examples/<app>/app.yaml` with today's values; a loader
  with clear validation messages (unknown realm, duplicate port or hostname,
  missing template, feature without its component).
- `apps.py` builds `APPS`, `REPLICATED_DATABASES` and `workloads()` from the
  loaded model; `deploy/environments/*/values.yaml` merge into
  `platform.yaml`.
- Build mode writes the resolved model into `bundle.json`; `target_render.py`
  and DR hosts read it from there.

Done when: rendered output equals the phase 1 output; no app is listed in
Python.

### Phase 3: Components and features (L)

- Introduce `Component` and `Feature` and the data policy. Move today's
  behaviour into `proxy`, `identity`, `database` and `login`.
- Installer, workload table, secrets, image list and DR group are collected
  from them instead of written per concept.
- Routing table: `shared-proxy.yaml.j2` loops over routes instead of one
  fixed server block per app.

Done when: rendered output equals phase 2 apart from explained ordering in
nginx; adding a fake feature in a test needs no change outside its class.

### Phase 4: Realms as configuration (M, changes behaviour)

- `identity` creates every realm in `platform.yaml` and secures it
  (`REALM_SECURITY`); `login` creates one client per app in its realm from
  platform defaults. The "copy the Todo client" logic goes away.
- Realm, issuer, JWKS URL and client ID reach backends through ConfigMaps
  and frontends through configuration or discovery; no realm constants.
- Promotion, failover and backup checks verify every realm's issuer.
- Example: Todo and Notes share `main`; a test install with a second realm.

Done when: the 17 hard-coded `realms/todo` are gone; SSO across a shared
realm and isolation across realms are tested end to end.

### Phase 5: App directories and generated units (M)

- Move Todo and Notes to `examples/todo` and `examples/notes` with their own
  `pod.yaml.j2`. Document the template variables as a contract and test it.
- Generate `.kube` units from one template per workload type; an app may
  override. (S5 chose literal units tested against the workload table; this
  reverses that choice, and the same test checks generated units.)
- **Validate app pod templates**: since apps bring their own YAML, the
  installer rejects what breaks platform guarantees (privileged, hostNetwork,
  hostPath, other apps' secrets, missing resource limits).

Done when: `deploy/quadlet` holds no per-app units; guard allow-list holds
only example apps.

### Phase 6: Static apps and help (M)

- Skeleton for a static app; `examples/help`.
- The `help` feature: own hostname, or a route under an app's hostname
  (`/help/`), and the help URL in the app's template values.

Done when: Help runs both ways in an end-to-end test, with no change to the
proxy template.

### Phase 7: Scripts read the model (S)

- `wait-ready.sh`, `preflight.sh`, acceptance and the replication firewall
  range read pods, hostnames and ports from `bundle.json`.

Done when: the guard allow-list is empty.

### Phase 8: Documentation and acceptance (M)

- Guides: "Add an app" and "Add a component or feature", each with one
  complete worked example and its tests. Skeletons for each app shape.
- ARCHITECTURE.md, LEARNING-GUIDE.md and the DR docs rewritten for the
  platform; Todo/Notes/Help described as examples.
- A full acceptance run; record the verdict.

## 5. Milestones

| Milestone | After phase | Proof |
|---|---|---|
| Neutral names | 1 | Full acceptance run (names touch DR and TLS) |
| Configuration-driven | 3 | Unit tests and offline install; rendered diff explained |
| Multi-realm | 4 | Full acceptance run plus multi-realm end-to-end tests |
| Open source ready | 8 | Full acceptance run; a third app added by following the guide alone |

The last proof matters most: someone adds a new app using only the guide and
touches no platform code.

## 6. Risks

| Risk | Mitigation |
|---|---|
| The abstraction grows beyond what a mid-level developer reads easily | One fixed interface, one module per component/feature, no metaclasses or dynamic imports; review each phase against AGENTS.md |
| DR regressions while code moves | Phase 0 fixtures, existing DR tests, acceptance at milestones; data policy is mandatory |
| App-supplied pod YAML weakens rootless/SELinux/secret guarantees | Template validation in phase 5 |
| Identity hostname change breaks TLS or failover | Done early (phase 1) and proven by acceptance before other work builds on it |
| Long-running branch drifts from ongoing backlog work | Phases are small and merged one by one |
| Path-based help on an app's hostname conflicts with app routes or CSP | Route conflicts are validation errors; security headers per route |

## 7. Not in scope

Kubernetes orchestration, Docker or Compose, a plugin marketplace or dynamic
loading, apps without PostgreSQL that still have a backend (room is left,
not built), specific shared services beyond proxy and identity, automatic
HA.

## 8. Questions for the reviewer

We want these challenged, not confirmed:

1. **Features and components instead of app kinds.** Is one interface for
   both right, or should they differ? Is the method list (section 2)
   complete and minimal?
2. **Data policy** (`replicated`, `local`, `none`). Is it enough for DR to
   handle unknown future services safely?
3. **Apps bring their own pod YAML.** Is template validation enough to keep
   platform guarantees, or should the platform generate the pod from
   `app.yaml` and let apps only fill in containers?
4. **Two configuration files** (`platform.yaml` operator, `app.yaml`
   developer). Right split, or should everything be in `platform.yaml`?
5. **Generated `.kube` units** instead of literal ones tested against the
   workload table. Worth the loss of readability?
6. **Realms created through the Keycloak admin API** instead of realm import
   files. Right default for repeatable installs and DR?
7. **Phase order.** Names and identity hostname first (behaviour change
   early, proven by acceptance), then configuration, then abstraction. Would
   another order reduce risk?
8. **Names.** `platform` as shared prefix, "app" for user workloads,
   "component" and "feature" for the extension points.
9. What is missing that will hurt when the first unknown service type
   arrives?
