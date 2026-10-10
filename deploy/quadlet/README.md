# Shared Quadlet resources and historical reference

This directory contains `app-network.network` and one `.kube.j2` template per
kind of workload: `app.kube.j2` (every app's pod), `postgres.kube.j2` (every
database, an app's or Keycloak's), `keycloak.kube.j2` and `shared-proxy.kube.j2`.
Each unit's files and `Requires=`/`After=` come from its workload in the model
(`apps.Platform.workloads()`, `Workload.requires`), so adding an app adds no file
here. The bundle build renders them with Jinja2 into `generated/target/quadlet`,
which an offline install and the DR tools fill in and install without Jinja2; build
mode renders the same files on the host. There are no role-local copies.
Host-specific units live beside the rendered YAML in
`~/.config/containers/systemd/platform-kube-runtime/`. See the [runtime guide](../runtime/README.md).

Persistent storage is declared by Kube YAML PVCs. No `.volume` Quadlets are
needed for the current workloads.

The seven historical .container units were retired after full acceptance of
688a0f6; see [the evidence](../../docs/history/ACCEPTANCE-688a0f6.md). The immutable
`quadlet-reference-v1` tag preserves the accepted per-container implementation.
Pre-retirement commit `c377161` also preserves migration/rollback tooling and PoCs.

Active install/rebuild guards and uninstall compatibility cleanup still recognize
legacy names. Their presence is intentional, not an alternate supported runtime.
No shared resource paths or active operational roles were changed by retirement.
