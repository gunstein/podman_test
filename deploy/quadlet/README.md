# Shared Quadlet resources and historical reference

This directory contains `app-network.network` and the seven canonical `.kube.j2`
templates. The bundle build renders them with Jinja2 into `generated/target/quadlet`,
which an offline install and the DR tools fill in and install without Jinja2; build
mode renders the same files on the host. There are no role-local copies.
Host-specific units live beside the rendered YAML in
`~/.config/containers/systemd/todo-kube-runtime/`. See the [runtime guide](../runtime/README.md).

Persistent storage is declared by Kube YAML PVCs. No `.volume` Quadlets are
needed for the current workloads.

The seven historical .container units were retired after full acceptance of
688a0f6; see [the evidence](../../docs/history/ACCEPTANCE-688a0f6.md). The immutable
`quadlet-reference-v1` tag preserves the accepted per-container implementation.
Pre-retirement commit `c377161` also preserves migration/rollback tooling and PoCs.

Active install/rebuild guards and uninstall compatibility cleanup still recognize
legacy names. Their presence is intentional, not an alternate supported runtime.
No shared resource paths or active operational roles were changed by retirement.
