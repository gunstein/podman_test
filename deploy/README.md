# Deployment sources

This directory contains the build-time workload definitions and installation
and operations tooling for the rootless Podman demo. The eight single-host workloads are
`todo-app`, `todo-postgres`, `notes-app`, `notes-postgres`, `help-app`, `keycloak`,
`keycloak-postgres` and `shared-proxy`. DR replicates, backs up, promotes and
rebuilds the Todo, Notes and Keycloak databases as one group.

| Path | Responsibility |
|---|---|
| `manifests/` | Jinja2 workload templates, one per workload type, shared by every app |
| `quadlet/` | One source for the network and one systemd unit template per kind of workload |
| `installer/` | Single-host Python installer, shared workload functions and nightly backups |
| `dr/` | app-ops (controller) and app_dr_host (each host): guarded DR/backup operations over plain SSH, the DR and backup tools, their timers (`dr/systemd`) and documentation |
| `scripts/` | Rendering, direct development, the acceptance lab (`scripts/lab`) and shared tools |
| `offline/` | OCI bundle builder, installer and offline requirements |
| `runtime/` | Runtime documentation, not generated manifests |

Run the following commands from the repository root. Packages retain this
layout; run their commands from the package root.

```bash
# Render production YAML without starting anything.
deploy/scripts/render-kube-runtime.sh

# Development uses local values and direct podman kube play/down.
# Every missing secret is generated; none require an interactive terminal.
deploy/scripts/dev/dev-up.sh
deploy/scripts/dev/dev-down.sh

# Production installation uses Quadlet/user-systemd.
PYTHONPATH=deploy/installer python3 -m app_installer install --mode server
```

Which apps run, their hostnames and the settings that vary by environment
(public port, log level) belong in `platform.yaml` at the repository root, and
what each app is in its `app.yaml` (`examples/<app>/`);
host and operational settings belong in the app-ops inventory. Settings that never
vary stay directly in the `.yaml.j2` template. Secrets stay
outside YAML and Git. Jinja2 renders both the Kube YAML in `manifests/*.yaml.j2`
and the `.kube.j2` host-integration files on the build host; the packages
carry them rendered, with `${TARGET_*}` placeholders for the values that vary
between hosts, which the installer fills in with the standard library alone.
Building needs Python 3.9+, Jinja2 and PyYAML; an offline target needs only
Python. See [installer usage](installer/README.md) and
[offline delivery](offline/README.md#target-values).
DR uses app-ops for remote transport, replication, backup and rebuild, calling
the same Python workload functions on each target. Shared infrastructure uses
`app-network` and `keycloak` consistently, including DR. No old-name runtime is
maintained.

Rendering defaults to `generated/kube-runtime/` for production and
`generated/dev/` for development. These ignored build outputs are separate from
source templates. Rendering produces twelve YAML files: Todo app/postgres/config,
Notes notes-app/notes-postgres/notes-config, Help help-app/help-config, keycloak, keycloak-postgres,
keycloak-config and shared-proxy. Both delivery packages carry `bundle.json`
and the rendered target files (`generated/target`); only the offline bundle
contains OCI image archives. Build and distribute both packages from the same
revision: the installer refuses a bundle of another format version.

For DR, write a small YAML inventory with the real host names, roles and
addresses; see [the app-ops inventory](dr/README.md#inventory).

See the [runtime guide](runtime/README.md), [app-ops operations](dr/README.md),
[offline delivery](offline/README.md), [architecture](../docs/ARCHITECTURE.md)
and [acceptance procedure](../docs/ACCEPTANCE.md).
Historical per-container definitions remain in `quadlet-reference-v1`; earlier
acceptance records describe the source layout at their own revisions.
