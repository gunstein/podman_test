# Project status

## Goal and architecture

A pedagogical rootless Podman Todo and Notes demo: Jinja2 renders Kube YAML;
user systemd manages seven .kube workloads (two apps, shared Keycloak and nginx,
three PostgreSQL databases replicated as one DR group). See [Architecture](docs/ARCHITECTURE.md) and
[Learning guide](docs/LEARNING-GUIDE.md).

## Acceptance

Full unchanged-revision Oracle Linux acceptance of the prior three-pod
architecture passed on `688a0f6` and again on `12c3bef`; see
[688a0f6](docs/history/ACCEPTANCE-688a0f6.md) and [12c3bef](docs/history/ACCEPTANCE-12c3bef.md)
for evidence and deviations. The later four-pod shared-proxy architecture
passed a process-level, evidence-light two-agent acceptance on `9e54cfb`; see
[the run record](docs/history/ACCEPTANCE-9e54cfb.md). That run exercised a destructive
standby rebuild again, so the topology recorded in the older runs is stale.
No topology was captured for `9e54cfb`; this is not a substitute for fresh
checks before operations.

The current seven-pod, three-database topology reached a REPAIRED FUNCTIONAL
PASS on `3fb897f` in a full two-VM agent run
([record](docs/ACCEPTANCE-3fb897f.md)). It found two standby-rebuild source
defects that were worked around procedurally; a CLEAN PASS with
[ACCEPTANCE.md](docs/ACCEPTANCE.md) requires fixing them and a new run. The
final topology of that run is VM 108 primary and VM 107 database-only standby;
verify roles freshly before any operation.

The same topology then reached a REPAIRED FUNCTIONAL PASS on `f1f07b5` with
app-ops in place of every Ansible playbook, in an autonomous agent run
([record](docs/history/ACCEPTANCE-f1f07b5.md)). No source changed. Three steps
did not pass as written and needed operator decisions: two guide ordering
defects (SSH host key pinned after the quarantine test uses it; the rebuild
host cannot reach the current primary's replication ports before
`rebuild-standby` publishes them) and a Guest Agent stop that ran before the
user manager's bus existed and succeeded on one retry. A CLEAN PASS still
needs these fixed and a new run. Final topology: VM 108 primary, VM 107
database-only standby.

## Current work and limitations

Legacy runtime and migration tooling are retired after that gate. Active runtime
and safety boundaries remain unchanged. Off-host backup, automatic HA and other
IdP adapters are not demonstrated production features.

The [Development journal](docs/history/DEVELOPMENT-JOURNAL.md) preserves earlier
checkpoints; historical next steps are not current instructions.
