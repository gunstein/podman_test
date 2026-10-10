# Platform design: from the Todo demo to a reusable app platform

Status: **proposal for discussion**. Nothing here is implemented yet. The
order of work is in [PLATFORM-PLAN.md](PLATFORM-PLAN.md). When the
design is agreed, ARCHITECTURE.md, LEARNING-GUIDE.md and AGENTS.md are updated
as the phases below land, and this document records the decisions.

## 1. Goal and decisions

Turn this branch into an open source platform for running small web apps on
rootless Podman, with offline delivery and DR, so that **adding an app means
adding an app directory and one entry in one configuration file**, and
**adding a new kind of app or shared service (one we do not know of yet)
means adding one well-defined module**, never edits spread through the
installer, proxy and DR code.

Decided:

- No backward compatibility. Existing names (`todo-proxy`, the `todo` realm,
  `todo-offline-*.tar.gz`, `~/.config/todo` and so on) may change freely.
  `todo` is never used as a general name.
- One shared Keycloak server, on its own hostname (`auth.<domain>`). An app
  chooses its realm: apps may share a realm (shared users, single sign-on) or
  have one of their own.
- A login app has frontend and backend in one pod and its PostgreSQL in a
  separate pod.
- Static apps exist: no login, backend or database. The first use is help
  text for the other apps, and there will be several ways to use help.
- More shared services and more kinds of apps will come later; which ones
  is not known yet. The platform must take new ones without redesign.
- Apps with a backend but no database come later; the design leaves room.
- Todo, Notes and Help stay in this repository as example apps.
- Abstraction is acceptable, but a moderately experienced Python developer
  must be able to read, maintain and extend the code.

The safety boundaries stay as they are: external secrets, rootless SELinux
storage, fencing, promotion, backup, PITR and standby rebuild acting on the
whole replicated database group.

## 2. Terms and names

| Term | Meaning | Examples |
|---|---|---|
| **platform** | The whole product, and the prefix of everything shared | `platform.yaml`, `platform-proxy`, `platform-offline-<tag>.tar.gz` |
| **component** | Shared infrastructure, at most one of each per installation | `proxy` (nginx), `identity` (Keycloak), later others |
| **app** | A user workload, configured by its own `app.yaml` | `todo`, `notes`, `help` |
| **feature** | Something an app uses from the platform | `database`, `login`, `help` |

Why "app" for user workloads: the code already says app (`app_installer`,
`app-ops`, `app_dr_host`, `app-network`, the `App` dataclass, `todo-app`
pods). "Service" is avoided because systemd units are already
`*.service`, and "the todo service" would be ambiguous with
`todo-app.service`.

Why "platform" as the prefix of shared resources: a shared resource belongs
to the platform, not to any app, and `app-proxy` would read as "an app's
proxy". The prefix is fixed in code, not the project's name, so the open
source project can be named and renamed without renaming secrets, volumes
and files on installed hosts.

## 3. Who owns what

The rule: **the platform owns everything DR, routing and identity depend on;
the app owns its own pod.**

| Owned by the platform | Owned by the app |
|---|---|
| PostgreSQL pods, roles, secrets, volumes, replication ports | Pod template (`pod.yaml.j2`) |
| Components: proxy, identity, later others | Source code and Containerfiles |
| Realms and OIDC clients | Migrations and `setup_roles.py` |
| The routing table and TLS | Its help pages, if it has any |
| `app-network`, generated `.kube` units | Optional `.kube` override |
| Installer, offline bundle, DR | Its `app.yaml` |

An app *declares* a feature; the platform delivers it. An app never brings
its own database or Keycloak YAML: promotion, backup and rebuild must know
exactly how every database in the group is built.

## 4. Configuration: two files

### 4.1 `platform.yaml`: the installation (operator-owned)

```yaml
components:
  identity:
    hostname: auth.example.test
    databasePort: 5432            # Keycloak's PostgreSQL replication port
    realms:
      main: {}
      partner: { import: realms/partner.json }   # optional extras

apps:
  todo:
    path: examples/todo           # may be outside this repository
    hostname: todo.example.test
    realm: main
    databasePort: 5433
  notes:
    path: examples/notes
    hostname: notes.example.test
    realm: main                   # shares users and SSO with todo
    databasePort: 5434
  help:
    path: examples/help
    hostname: help.example.test

environments:
  local: { publicPort: 8443, logLevel: debug }
  prod:  { publicPort: 443,  logLevel: info }
```

Replication ports are explicit, not derived from list order: reordering the
list must never renumber a database. The loader rejects duplicates.

### 4.2 `app.yaml`: the app (app-developer-owned)

```yaml
# examples/todo/app.yaml
images: [backend, frontend]       # built from <app dir>/<image>/Containerfile
features:
  database: {}
  login: { api: /api/todos }
  help: { from: help, path: /help/ }   # see section 7
```

```yaml
# examples/help/app.yaml
images: [site]
features: {}                      # static: no database, no login
```

There are no fixed app kinds in code. "Login app with database" and
"static site" are **skeleton directories** to copy from, each a valid
combination of features.

### 4.3 What disappears

- `apps.py` stops listing apps in code; it loads and validates the two files.
  The ordered workload table from S5 (`apps.workloads()`) stays the one place
  that decides start and stop order, built from components and apps.
- `deploy/environments/*/values.yaml` merge into `platform.yaml`.
- Hard-coded lists in `wait-ready.sh`, `preflight.sh` and the acceptance
  firewall range are read from `bundle.json`.

Build mode reads the YAML (it already has PyYAML and Jinja2) and writes the
resolved result into `bundle.json`. Offline targets keep needing only the
Python standard library.

## 5. Components and features in code

This is the extension point that keeps new kinds of configuration out of the
rest of the code. Both are plain classes with the same small set of
questions; each lives in its own module, and a fixed dictionary lists them
(no plugin framework, no dynamic imports).

```python
class Feature:
    """Something an app uses from the platform."""
    name = ""
    requires_component = None          # e.g. "identity"

    def validate(self, app, settings, platform): ...   # plain error messages
    def workloads(self, app, settings): return ()      # extra pods, e.g. its database
    def replicated_databases(self, app, settings): return ()
    def template_values(self, app, settings, platform): return {}   # for pod.yaml.j2
    def routes(self, app, settings, platform): return ()            # proxy routes
    def secrets(self, app, settings): return ()
    def requires(self, app, settings): return ()       # units its .kube needs first
```

```python
class Component:
    """Shared infrastructure, at most one per installation."""
    name = ""
    def validate(self, settings, platform): ...
    def workloads(self, settings): return ()
    def replicated_databases(self, settings): return ()
    def routes(self, settings, platform): return ()
    def secrets(self, settings): return ()
    def configure(self, settings, platform): ...       # after start, e.g. realms and clients
```

```python
FEATURES = {"database": Database(), "login": Login(), "help": Help()}
COMPONENTS = {"proxy": Proxy(), "identity": Identity()}
```

Everything else only collects answers: the replicated database group is
every `replicated_databases()`; the proxy configuration is every `routes()`;
the start order is every `workloads()`; the generated `.kube` unit gets
`Requires=` from `requires()`. Adding a kind of configuration is a new class,
its templates and its tests; the installer, proxy template and DR code are
not touched.

A later "backend without database" app needs no new kind: it simply does not
list `database`. Its pod template and skeleton are the only new parts.

## 6. The routing table

nginx is rendered from a list of routes instead of one fixed server block per
app:

```python
Route(hostname="todo.example.test", path="/api/", upstream="todo-backend:8000")
Route(hostname="todo.example.test", path="/help/", upstream="help-site:8080")
Route(hostname="todo.example.test", path="/", upstream="todo-frontend:8080")
Route(hostname="auth.example.test", path="/auth/", upstream="keycloak:8080")
```

The proxy template loops over hostnames and their routes. Every hostname is
added to the TLS certificate. Conflicting routes (same hostname and path)
are a validation error.

## 7. Help

The routing table is what lets help be used in several ways without new
proxy code:

| Way | Configuration |
|---|---|
| Own hostname | the help app's own `hostname` in `platform.yaml` |
| Under an app | `help: { from: help, path: /help/ }` in the app's `app.yaml` adds a route on the app's hostname |
| Help per app in one site | the help site has a directory per app; the route maps `/help/` to `/<app>/` |
| Linked from the frontend | the app's template values carry the help URL, so the frontend can link to it |

New ways are new options on the `help` feature, not new proxy code.

## 8. Adding a new kind of shared service or app

Which shared services and app kinds will come is not known yet. The design
does not guess; it fixes what a new one must answer, so it fits without
changes elsewhere:

| Question | Answered by |
|---|---|
| Which pods does it run, and what must start first? | `workloads()`, `requires()` |
| Which hostnames and paths does it serve? | `routes()` |
| Which secrets does it need? | `secrets()` |
| What does an app's pod template get from it? | `template_values()` |
| What must be set up after it starts? | `configure()` |
| What happens to its data in DR? | its data policy, below |

Every component and feature states one **data policy**, because DR must
never be guessed:

- **replicated**: its data is PostgreSQL in the replicated group
  (`replicated_databases()`), promoted, backed up and rebuilt with the rest.
- **local**: it keeps data in its own volume that is not replicated; a
  promoted standby starts it empty, and the documentation says so.
- **none**: it keeps no data.

journald stays the base for every container's output whatever is added
(`LogDriver=journald` in every generated unit, `docs/LOGGING.md`).

The "add a component or feature" guide (phase 8) walks through one complete
example with its tests, so the next developer copies a working pattern.

## 9. Realms

- The identity component creates every realm in `platform.yaml`, applies the
  existing login protection (`REALM_SECURITY`) to each, and the `login`
  feature creates one client per app in its realm from platform-defined
  defaults. Copying the Todo client as a template goes away.
- An optional import file per realm adds users, themes or settings.
- Each app's template values carry its realm's issuer, JWKS URL and its
  client ID; frontends never hold a realm constant.
- DR needs no data change: every realm lives in Keycloak's database, already
  in the replicated group. Promotion and failover checks verify every realm's
  issuer instead of only `realms/todo`.
- Apps in different realms have separate users and no shared login.

## 10. Phases

Every phase keeps all tests green and ends with a working installation.

1. **Decision and guard.** Agree this document. Update AGENTS.md. Add a
   guard test that fails when `todo` or `notes` appear in platform code
   outside the example apps, with an allow-list that shrinks phase by phase.
2. **Configuration from YAML and the `platform` prefix.** Load
   `platform.yaml` and `app.yaml` into the existing dataclasses. Replace
   `IDENTITY_APP` with the identity component's own hostname and the `todo-`
   prefix of shared constants (`PROXY_IMAGE`, `PROXY_TLS_SECRETS`,
   `NGINX_TLS_VOLUME`, `~/.config/todo` ...) with `platform-`.
3. **Features and components.** Introduce the two classes and move today's
   behaviour into `database`, `login`, `proxy` and `identity`. The routing
   table replaces the per-app server block.
4. **Realms as configuration** (section 9).
5. **App directories.** Move Todo and Notes to `examples/`, their pod
   templates into their directories, and generate `.kube` units. S5 kept the
   units written out literally and tested them against the workload table;
   generating them reverses that choice, and the same test checks the
   generated units.
6. **Help.** A static example app and the `help` feature (section 7).
7. **Scripts read `bundle.json`.** `wait-ready`, `preflight`, acceptance and
   firewall ranges.
8. **Documentation.** "Add an app" and "add a component or feature" guides,
   skeletons, updated ARCHITECTURE and LEARNING-GUIDE, then a full
   acceptance run.

## 11. Proposed AGENTS.md direction

Replace the Todo-centred goal with something like:

> Maintain a reusable rootless Podman Kube platform for small web apps. The
> platform owns databases, identity, routing, other shared components,
> installation and DR; an app is a directory with `app.yaml`, a pod
> template and its source, plus one entry in `platform.yaml`. Adding an app
> must not require platform code changes; a new kind of configuration is a
> new feature or component module. Todo, Notes and Help are example apps.

And replace "Prefer simple, pedagogical solutions over abstraction" with:

> Abstraction is welcome where it removes per-app code, but keep it readable
> for a moderately experienced Python developer: plain classes and
> dataclasses, explicit validation with clear messages, a fixed list of
> features and components, no plugin frameworks or dynamic imports.

## 12. Open questions

1. **Names.** `platform` as the shared prefix and "app" for user workloads,
   as proposed in section 2?
