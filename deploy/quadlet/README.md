# Shared Quadlet resources and historical reference

This directory contains `app-network.network` and the four canonical `.kube.j2`
templates. Every Ansible runtime role uses these same files; there are no
role-local copies. Ansible renders host-specific port bindings and installs the
units beside the rendered workload YAML. See the [runtime guide](../runtime/README.md).

Persistent storage is declared by Helm/Kube PVCs. No `.volume` Quadlets are
needed for the current workloads.

The seven historical .container units were retired after full acceptance of
688a0f6; see [the evidence](../../docs/ACCEPTANCE-688a0f6.md). The immutable
`quadlet-reference-v1` tag preserves the accepted per-container implementation.
Pre-retirement commit `c377161` also preserves migration/rollback tooling and PoCs.

Active install/rebuild guards and uninstall compatibility cleanup still recognize
legacy names. Their presence is intentional, not an alternate supported runtime.
No shared resource paths or active operational roles were changed by retirement.
