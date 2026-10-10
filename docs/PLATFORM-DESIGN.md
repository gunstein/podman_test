# Platform design: from the Todo demo to a reusable service platform

Status: **proposal for discussion**. Nothing here is implemented yet. When the
design is agreed, ARCHITECTURE.md, LEARNING-GUIDE.md and AGENTS.md are updated
as the phases below land, and this document records the decisions.

## 1. Goal

Turn this branch into an open source platform for running a set of small web
services on rootless Podman, with offline delivery and DR, so that **adding a
service means adding a service directory and one entry in one configuration
file**. No Python, shell, proxy or DR code changes for a new service.

Todo and Notes stop being the product and become example services.

Decisions already taken:

- No backward compatibility. Existing names (`postgres.yaml`, `todo-proxy`,
  the `todo` realm, `todo-offline-*.tar.gz` and so on) may change freely.
- One shared Keycloak server for all services.
- A service chooses its realm: several services may share one realm (shared
  users, single sign-on), and a service may have a realm of its own.
- Two kinds of service now:
  - **webapp**: frontend and backend in one pod, PostgreSQL in its own pod,
    login through Keycloak. This is what Todo and Notes are today.
  - **static**: static web pages only, no login, backend or database. The
    first use is help text for the other services.
- A kind with backend but no database comes later; the design must leave
  room for it without building it now.
- Abstraction is acceptable, but a moderately experienced Python developer
  must be able to read, maintain and extend the code.

The safety boundaries stay as they are: external secrets, rootless SELinux
storage, fencing, promotion, backup, PITR and standby rebuild acting on the
whole database group.

## 2. Who owns what

The core rule: **the platform owns everything DR depends on; the service owns
its own pod.**

| Owned by the platform | Owned by the service |
|---|---|
| PostgreSQL pods, roles, secrets, volumes, replication ports | Pod template (`pod.yaml.j2`) |
| Keycloak, its database, realms and clients | Source code and Containerfiles |
| nginx proxy, TLS, routing by hostname | Migrations and `setup_roles.py` |
| `app-network`, `.kube` units (generated) | Optional `.kube` override |
| Installer, offline bundle, DR (`app-ops`, `app_dr_host`) | Its `service.yaml` |

A service *declares* that it needs a database; it never brings its own
database YAML. Promotion, backup and rebuild must know exactly how every
database in the group is built, so the PostgreSQL template stays one shared,
platform-owned template.

## 3. Configuration: two files, clear roles

### 3.1 `platform.yaml`: the installation (one file, operator-owned)

Everything that varies per installation lives here: which services run,
their hostnames, realms and ports, and environment differences.

```yaml
name: myplatform              # prefix for shared resources: myplatform-proxy,
                              # myplatform-offline-<tag>.tar.gz, myplatform-tls ...
identity:
  hostname: auth.example.test # Keycloak's public host and the OIDC issuer host
  databasePort: 5432          # Keycloak's own PostgreSQL replication port

realms:
  main: {}                    # created and secured by the installer
  partner:
    import: realms/partner.json   # optional: users, themes, extra settings

services:
  todo:
    path: examples/todo       # service directory; may be outside this repo
    hostname: todo.example.test
    realm: main
    databasePort: 5433
  notes:
    path: examples/notes
    hostname: notes.example.test
    realm: main               # shares users and SSO with todo
    databasePort: 5434
  help:
    path: examples/help
    hostname: help.example.test   # static: no realm, no databasePort

environments:
  local: { publicPort: 8443, logLevel: debug }
  prod:  { publicPort: 443,  logLevel: info }
```

Replication ports are explicit, not derived from list order: reordering the
list must never renumber a database. The loader rejects duplicates.

### 3.2 `service.yaml`: the service (service-developer-owned)

Facts that belong to the service wherever it is installed:

```yaml
kind: webapp                  # webapp | static
images: [backend, frontend]   # built from <service dir>/<image>/Containerfile
api: /api/todos               # routed to the backend (webapp only)
```

A static service:

```yaml
kind: static
images: [site]
```

### 3.3 What disappears

- `deploy/installer/app_installer/apps.py` stops listing applications in code;
  it loads and validates the two files instead. The ordered workload table
  from S5 (`apps.workloads()`) stays the one place that decides start and
  stop order; it is built from the loaded services instead of `APPS`.
- `deploy/environments/*/values.yaml` merge into `platform.yaml`.
- Hard-coded lists in `wait-ready.sh`, `preflight.sh` and the acceptance
  firewall range (`5432-5434`) are read from `bundle.json`.

Build mode reads the YAML (it already has PyYAML and Jinja2) and writes the
resolved result into `bundle.json`. Offline targets keep needing only the
Python standard library, exactly as today.

## 4. Service directory and the template contract

```text
examples/todo/
  service.yaml
  pod.yaml.j2          # the service's pod
  pod.kube.j2          # optional; generated from the kind's default otherwise
  backend/             # Containerfile, main.py, migrate.py, migrations/, setup_roles.py
  frontend/            # Containerfile, index.html, app.js, auth.js ...
```

The platform renders `pod.yaml.j2` with a documented set of variables, for
example:

| Variable | Example | Kinds |
|---|---|---|
| `service.name`, `service.pod` | `todo`, `todo-app` | all |
| `images.<name>` | `localhost/todo-backend:<tag>` | all |
| `database.host`, `database.name` | `todo-postgres`, `todo` | webapp |
| `database.role(...)`, `secrets.<role>` | `todo_migrator`, secret names | webapp |
| `oidc.issuer`, `oidc.jwks_url`, `oidc.audience` | from the service's realm | webapp |

Skeletons per kind live in `services/_templates/<kind>/` (or a similar
location) to copy from. The variable list is part of the public contract and
is tested.

**Service contract** (what a webapp must do, checked by tests where possible):
`/health` and `/ready` on the backend, migrations as an init container,
separate bootstrap/migration/runtime roles, passwords read from files, OIDC
audience checked against its own client. A static service serves HTTP on its
container port and nothing else.

## 5. Kinds in code

A kind is a small Python class that answers a fixed set of questions; adding
a kind means adding one class and one skeleton directory.

```python
@dataclass(frozen=True)
class Kind:
    name: str
    has_database: bool
    has_login: bool
    proxy_routes: tuple   # e.g. ("/api/", "/health", "/ready") -> backend, "/" -> frontend

WEBAPP = Kind("webapp", has_database=True,  has_login=True,  proxy_routes=...)
STATIC = Kind("static", has_database=False, has_login=False, proxy_routes=...)
# Later: API = Kind("api", has_database=False, has_login=True, ...)
```

Everything else asks the kind instead of the service name: the replicated
database group is the services whose kind has a database, plus Keycloak's;
the Keycloak setup creates clients for services whose kind has login; the
proxy template loops over each service's routes; the generated `.kube` unit
`Requires=` the database and Keycloak only when the kind needs them.

No plugin entry points, metaclasses or dynamic imports: one module with the
kinds, one module that loads and validates the configuration with plain error
messages ("service help: kind static must not set databasePort").

## 6. Realms

- The installer creates every realm in `platform.yaml`, applies the existing
  login protection (`REALM_SECURITY`) to each, and creates one client per
  login service in that service's realm from platform-defined client
  defaults. The current "copy the Todo client as a template" logic goes away.
- An optional realm import file adds users, themes or extra settings.
- Each service gets its realm's issuer and JWKS URL in its ConfigMap; frontends
  read the realm from configuration or discovery, never from a constant.
- DR needs no data change: every realm lives in Keycloak's database, which
  is already in the replicated group. The promotion and failover checks
  verify each realm's issuer instead of only `realms/todo`.
- Services in different realms have separate users and no shared login.

## 7. Phases

Every phase keeps all tests green and ends with a working installation.

1. **Decision and guard.** Agree this document. Update AGENTS.md (goal,
   constraints, "add a service" rule). Add a guard test that fails when
   `todo` or `notes` appear in platform code outside the example services;
   it starts with an allow-list that shrinks phase by phase.
2. **Configuration from YAML.** Load `platform.yaml` and `service.yaml` into
   the existing dataclasses. S5 already removed the bare manifest names and
   named the shared resources directly; what remains is to replace
   `IDENTITY_APP` with `identity` and the `todo-` prefix of the shared
   constants (`PROXY_IMAGE`, `PROXY_TLS_SECRETS`, `NGINX_TLS_VOLUME` and so
   on) with `name` from `platform.yaml`.
3. **Realms as configuration** (section 6).
4. **Service directories.** Move Todo and Notes to `examples/`, their pod
   templates into their directories, and generate `.kube` units. S5 chose to
   keep the units written out literally and test them against the workload
   table; generating them from one template per kind reverses that choice,
   and the same test then checks the generated units.
5. **Static kind.** Add the kind and an `examples/help` service.
6. **Scripts read `bundle.json`.** `wait-ready`, `preflight`, acceptance and
   firewall ranges.
7. **Documentation.** "Add a service" guide, kind skeletons, updated
   ARCHITECTURE and LEARNING-GUIDE, then a full acceptance run.

## 8. Proposed AGENTS.md direction

Replace the Todo-centred goal with something like:

> Maintain a reusable rootless Podman Kube platform for small web services.
> The platform owns databases, identity, proxy, installation and DR; a
> service is a directory with `service.yaml`, a pod template and its source,
> plus one entry in `platform.yaml`. Adding a service must not require
> changes to platform code. Todo, Notes and Help are example services.

And replace "Prefer simple, pedagogical solutions over abstraction" with:

> Abstraction is welcome where it removes per-service code, but keep it
> readable for a moderately experienced Python developer: plain dataclasses,
> explicit validation, no plugin frameworks or dynamic imports.

## 9. Open questions

1. **Platform name.** It replaces the `todo-` prefix on shared resources and
   the bundle names.
2. **Help pages: own hostname or path?** `help.example.test` fits the current
   proxy pattern directly. Serving them under each service, such as
   `todo.example.test/help/`, needs path-based routes in the proxy template.
3. **Where services live.** Recommendation: examples in this repository, and
   `path:` in `platform.yaml` may point to services in other repositories.
4. **Identity hostname.** Recommendation: a dedicated `auth.<domain>` instead
   of borrowing the first service's hostname, as Todo's does today.
