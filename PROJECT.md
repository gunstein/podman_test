# Project status

## Goal and architecture

A pedagogical rootless Podman Todo demo: Helm renders Kube YAML; user systemd
manages three .kube workloads. See [Architecture](docs/ARCHITECTURE.md) and
[Learning guide](docs/LEARNING-GUIDE.md).

## Acceptance

Full unchanged-revision Oracle Linux acceptance of the prior three-pod
architecture passed on `688a0f6` and again on `12c3bef`; see
[688a0f6](docs/ACCEPTANCE-688a0f6.md) and [12c3bef](docs/ACCEPTANCE-12c3bef.md)
for evidence and deviations. The current four-pod shared-proxy architecture
passed a process-level, evidence-light two-agent acceptance on `9e54cfb`; see
[the run record](docs/ACCEPTANCE-9e54cfb.md). That run exercised a destructive
standby rebuild again, so the topology recorded in the older runs is stale.
No topology was captured for `9e54cfb`; this is not a substitute for fresh
checks before operations.

## Current work and limitations

Legacy runtime and migration tooling are retired after that gate. Active runtime
and safety boundaries remain unchanged. Off-host backup, automatic HA and other
IdP adapters are not demonstrated production features.

The [Development journal](docs/history/DEVELOPMENT-JOURNAL.md) preserves earlier
checkpoints; historical next steps are not current instructions.
