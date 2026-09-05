# Shared Quadlet resources and historical reference

This directory now contains only the active shared network/volume definitions
and this index. Runtime .kube units live in [kube/runtime](../kube/runtime/README.md).

The seven historical .container units were retired after full acceptance of
688a0f6; see [the evidence](../docs/ACCEPTANCE-688a0f6.md). The immutable
`quadlet-reference-v1` tag preserves the accepted per-container implementation.
Pre-retirement commit `c377161` also preserves migration/rollback tooling and PoCs.

Active install/rebuild guards and uninstall compatibility cleanup still recognize
legacy names. Their presence is intentional, not an alternate supported runtime.
No shared resource paths or active operational roles were changed by retirement.
