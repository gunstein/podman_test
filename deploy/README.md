# Deployment sources

This directory contains the build-time workload definitions and installation
and operations tooling for the rootless Podman demo. The seven single-host workloads are
`todo-app`, `todo-postgres`, `notes-app`, `notes-postgres`, `keycloak`,
`keycloak-postgres` and `shared-proxy`. DR replicates, backs up, promotes and
rebuilds the Todo, Notes and Keycloak databases as one group.

| Path | Responsibility |
|---|---|
| `manifests/` | Jinja2 workload templates, one per workload type, shared by every app |
| `environments/local/values.yaml`, `environments/prod/values.yaml` | Non-secret workload overrides, shared by every workload |
| `quadlet/` | One source for the network and seven systemd workload templates |
| `installer/` | Single-host Python installer and shared workload functions |
| `ops/` | app-ops: guarded DR/backup operations over plain SSH, and their documentation |
| `scripts/` | Rendering, direct development and operational tools |
| `offline/` | OCI bundle builder, installer and offline requirements |
| `runtime/` | Runtime documentation, not generated manifests |

Run the following commands from the repository root. Packages retain this
layout; run their commands from the package root.

```bash
# Render production YAML without starting anything.
deploy/scripts/render-kube-runtime.sh

# Development uses local values and direct podman kube play/down.
# Every missing secret is generated; none require an interactive terminal.
deploy/scripts/dev-up.sh
deploy/scripts/dev-down.sh

# Production installation uses Quadlet/user-systemd.
PYTHONPATH=deploy/installer python3 -m app_installer install --mode server
```

Workload settings that vary by environment belong in `environments/*/values.yaml`;
host and operational settings belong in the app-ops inventory. Settings that never
vary stay directly in the `.yaml.j2` template. Secrets stay
outside YAML and Git. Jinja2 renders both the Kube YAML in `manifests/*.yaml.j2`
and the `.kube.j2` host-integration files; installation only copies the
already-rendered Kube YAML, without templating it a second time.
The package needs Python 3.9+ and Jinja2. See [installer usage](installer/README.md).
DR uses app-ops for remote transport, replication, backup and rebuild, calling
the same Python workload functions on each target. Shared infrastructure uses
`app-network` and `keycloak` consistently, including DR. No old-name runtime is
maintained.

Rendering defaults to `generated/kube-runtime/` for production and
`generated/dev/` for development. These ignored build outputs are separate from
source templates. Rendering produces eight YAML files: Todo app/postgres/config,
Notes notes-app/notes-postgres/notes-config, keycloak and shared-proxy. Both delivery packages contain rendered YAML and the shared
Quadlet templates. Only the offline bundle contains OCI image archives. Rebuild
and distribute both packages together after this layout change: older bundles
with YAML under `kube/runtime/` do not match these tools.

For DR, write a small YAML inventory with the real host names, roles and
addresses; see [the app-ops inventory](ops/README.md#inventory).

See the [runtime guide](runtime/README.md), [app-ops operations](ops/README.md),
[offline delivery](offline/README.md), [architecture](../docs/ARCHITECTURE.md)
and [acceptance procedure](../docs/ACCEPTANCE.md).
Historical per-container definitions remain in `quadlet-reference-v1`; earlier
acceptance records describe the source layout at their own revisions.
