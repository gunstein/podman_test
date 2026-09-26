# Project status

## Goal and architecture

A pedagogical rootless Podman Todo and Notes demo: Jinja2 renders Kube YAML;
user systemd manages seven .kube workloads (two apps, shared Keycloak and nginx,
three PostgreSQL databases replicated as one DR group). See [Architecture](docs/ARCHITECTURE.md) and
[Learning guide](docs/LEARNING-GUIDE.md).

## Acceptance

**Current verdict: CLEAN PASS** on `2165933` for the seven-pod, three-database
topology, with app-ops (plain SSH) in place of every Ansible playbook, in a
full two-VM agent run ([record](docs/history/ACCEPTANCE-2165933.md)). Every
phase and step passed as written on a clean revision, with no source change
and no retry: install, standby bootstrap, quarantine rehearsal, fencing with
`pve_lab.py fence`, group promotion, application failover, backup and isolated
PITR, rebuild of the old primary and sequential reboots. Final topology: VM 108
primary with application and backup, VM 107 database-only standby; verify
roles freshly before any operation.

How it got there, newest first:

- `21659331` run 9: CLEAN PASS ([record](docs/history/ACCEPTANCE-2165933.md)).
- `3bc5924` run 8: BLOCKED in phase 9. A new read-only rebuild preflight
  check could not tell an open replication path from a blocked one under the
  quarantine firewall; it now runs inside the rebuild after the primary
  publishes its ports. Nothing was deleted. No separate record.
- `0604c56` run 7: REPAIRED FUNCTIONAL PASS
  ([record](docs/history/ACCEPTANCE-0604c56.md)); `rebuild-standby` was started
  out of order, refused before deleting anything, and was rerun.
- `1b1d345` run 6: functional pass, not clean
  ([record](docs/history/ACCEPTANCE-1b1d345.md)); part of the fencing step was
  skipped, which led to fencing as one command.
- `f1f07b5`: REPAIRED FUNCTIONAL PASS, the first full app-ops run
  ([record](docs/history/ACCEPTANCE-f1f07b5.md)).
- `3fb897f`: REPAIRED FUNCTIONAL PASS with Ansible
  ([record](docs/history/ACCEPTANCE-3fb897f.md)). Its two standby-rebuild
  defects are fixed and were exercised by run 9.
- `9e54cfb`: the earlier four-pod shared-proxy architecture, a process-level,
  evidence-light two-agent run ([record](docs/history/ACCEPTANCE-9e54cfb.md)).
- `688a0f6` and `12c3bef`: full unchanged-revision acceptance of the prior
  three-pod architecture ([688a0f6](docs/history/ACCEPTANCE-688a0f6.md),
  [12c3bef](docs/history/ACCEPTANCE-12c3bef.md)).

## Current work and limitations

Legacy runtime and migration tooling are retired. Active runtime and safety
boundaries remain unchanged. app-ops (`deploy/ops`, plain SSH) is the only DR
tool; the Ansible playbooks were retired after its CLEAN PASS, and Git history
keeps them. Planned work, its order
and its principles are in the [backlog](docs/BACKLOG.md), with a one-page
[target picture](docs/TARGET-PICTURE.md). Off-host backup, automatic HA and
other IdP adapters are not demonstrated production features.

The [Development journal](docs/history/DEVELOPMENT-JOURNAL.md) preserves earlier
checkpoints; historical next steps are not current instructions.
