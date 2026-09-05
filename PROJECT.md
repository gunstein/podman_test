# Project status

## Goal and architecture

A pedagogical rootless Podman Todo demo: Helm renders Kube YAML; user systemd
manages three .kube workloads. See [Architecture](docs/ARCHITECTURE.md) and
[Learning guide](docs/LEARNING-GUIDE.md).

## Acceptance

Full unchanged-revision Oracle Linux acceptance passed on `688a0f6`.
See [the run record](docs/ACCEPTANCE-688a0f6.md) for evidence and deviations.
The tested lab ended with VM108 as primary and VM107 as quarantined standby.
This is recorded topology, not a substitute for fresh checks before operations.

## Current work and limitations

Legacy runtime and migration tooling are retired after that gate. Active runtime
and safety boundaries remain unchanged. Off-host backup, automatic HA and other
IdP adapters are not demonstrated production features.

The [Development journal](docs/history/DEVELOPMENT-JOURNAL.md) preserves earlier
checkpoints; historical next steps are not current instructions.
