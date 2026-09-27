# Install picture: from source to running pods

One page showing how the solution is built, packed, moved and installed today.
[TARGET-PICTURE.md](TARGET-PICTURE.md) is the matching page for DR.

## Build on a connected machine

```mermaid
flowchart LR
  subgraph SRC["Git checkout"]
    j2["Kube YAML templates<br>deploy/manifests/*.yaml.j2"]
    values["values.yaml<br>prod: hostname, port"]
    code["Containerfiles<br>backend, frontend,<br>nginx, Keycloak"]
    q["Quadlet templates<br>deploy/quadlet/*.kube.j2"]
    inst["Python installer<br>app_installer"]
    ops["app-ops<br>deploy/ops"]
  end

  j2 & values -->|"Jinja2<br>render-kube-runtime.sh"| yaml["10 plain YAML files<br>for 7 pods"]
  code -->|"podman build<br>+ pull postgres:17.11"| img["7 OCI archives"]

  yaml & img & q & inst --> bundle[["todo-offline-m12.tar.gz<br>everything for one host"]]
  yaml & q & inst & ops --> opspkg[["todo-operations.tar.gz<br>DR tools, no images"]]
```

Both packages get a `VERSION` file (Git revision and `clean`/`dirty`), a
`SHA256SUMS` file for every file inside, and an external `.sha256` next to the
archive. Build only from a clean checkout; `dirty` is for diagnosis.

## Move and install on the target (no internet)

```mermaid
flowchart LR
  a["Copy archive<br>+ .sha256"] --> b["sha256sum -c<br>then tar -x"]
  b --> c["fapolicyd: trust the<br>installer .py files<br>(exact files only)"]
  c --> d["sh install.sh<br>--publish-address IP"]
  d --> e["SHA256SUMS<br>+ preflight.sh"]
  e --> f["python3 -m<br>app_installer install"]
```

`preflight.sh` changes nothing. It checks Podman rootless, subuid/subgid, the
Quadlet generator, `systemctl --user`, Python with Jinja2, free ports
5432-5434, 8080 and 8443, and reports disk and memory.

## What the Python installer does

```mermaid
flowchart TB
  s1["1. Refuse unsupported hosts:<br>DR host, old per-container<br>Quadlets, old Podman"] --> s2["2. Podman secrets:<br>keep existing, generate<br>missing (32 characters)"]
  s2 --> s3["3. Load missing images<br>from the bundle"]
  s3 --> s4["4. Write YAML (0600) and<br>.kube units (0644), only<br>changed files; daemon-reload"]
  s4 --> s5["5. Stop only services whose<br>file or image changed"]
  s5 --> s6["6. Start in order: databases,<br>roles, Keycloak, apps,<br>roles again, nginx"]
  s6 --> s7["7. Keycloak: clients,<br>redirects, lockout and<br>password policy"]
  s7 --> s8["8. Check every unit runs<br>from the expected .kube file"]
```

Run it again with the same arguments and nothing changes (`changed: false`).
The installer never asks for a password and never changes firewalld.

## What ends up on the host

| Where | What |
|---|---|
| `~/.config/containers/systemd/` | `app-network.network` and the `todo-kube-runtime/` folder |
| `…/todo-kube-runtime/*.yaml` | The rendered pods and ConfigMaps, as built (0600) |
| `…/todo-kube-runtime/*.kube` | Seven Quadlet units; user systemd starts them at boot |
| `podman secret ls` | Database, migrator, app and Keycloak admin passwords; never in YAML or the bundle |
| Podman volumes | Three database volumes and `todo-nginx-data` (TLS key and certificate) |
| Ports | 8443 on the chosen IP; 8080 and 5432-5434 on localhost only |

## Same YAML, three ways to run it

| | Development | One production host | Two hosts (DR) |
|---|---|---|---|
| Command | `dev-up.sh` / `dev-down.sh` | `sh install.sh` | `app-ops` from the client |
| Runs pods with | `podman kube play` directly | Quadlet `.kube` units | Quadlet `.kube` units |
| Images | Built from the checkout | Loaded from the bundle | Loaded from the bundle |
| YAML | Rendered with `local` values | Rendered at build, `prod` values | The same as one host |
| Code that installs | `app_installer` | `app_installer` | `app_installer` on each host, over SSH |

One implementation: app-ops installs nothing itself. It calls the same
`app_installer` CLI on each host, then adds replication, promotion and backup.
