# Project status

## Goal and architecture

A pedagogical rootless Podman Todo and Notes demo: Jinja2 renders Kube YAML;
user systemd manages seven .kube workloads (two apps, shared Keycloak and nginx,
three PostgreSQL databases replicated as one DR group). See [Architecture](docs/ARCHITECTURE.md),
[Learning guide](docs/LEARNING-GUIDE.md) and the one-page
[install picture](docs/INSTALL-PICTURE.md) of the production and development paths.

## Acceptance

**Current verdict: CLEAN PASS** on `196c2c7` for the seven-pod, three-database
topology in a full two-VM agent run ([record](docs/history/ACCEPTANCE-196c2c7.md)),
with replication over TLS, Keycloak lockout and password policy, the nginx
security headers, and the acceptance run itself done through
`deploy/scripts/lab/acceptance.py`. Its `report full` found all 104 steps PASS
on one clean revision and compared them with the agent guide: every step and
log it names, nothing else. Every step ran through `acceptance.py step`, which
runs the guide's line as written and only after the step before it passed,
and each product log records its exact command. Install, standby bootstrap,
quarantine rehearsal, fencing, the one `failover` command (group promotion,
application tier, backup, services and the login page), backup and isolated
PITR, rebuild of the old primary and sequential reboots all passed as written.
CI also runs the Todo API as `todo_app` and the whole stack with the browser
tests on Podman 5.7. The single-host installer (`deploy/installer`) and DR
(`deploy/dr`) are separate in the tree, and the code clean-up since `24b32ee`
is accepted: the naming model (R5), paths and constants in one place (S2), one
way to run commands and SQL (S3), clearer DR class names (S5), the shared
PostgreSQL image and DNS-name hostnames.
Final topology: VM 108 primary with application and backup, VM 107
database-only standby; verify roles freshly before any operation.

How it got there, newest first:

- `196c2c7` run 27: CLEAN PASS ([record](docs/history/ACCEPTANCE-196c2c7.md)),
  accepting R5, S2, S3, S5 and the rest since `24b32ee`, the first run with
  every step through `acceptance.py step`.
- `37c49c1` run 26: stopped in phase 6 by the agent, which ran `06-6` and
  `06-10` without their confirmation arguments and went on past the
  failure; the product refused both before any change, no promotion
  ([record](docs/history/ACCEPTANCE-37c49c1.md)). R5, S2, S3, S5 and the
  rest since `24b32ee` still wait for a clean run.
- `24b32ee` run 25: CLEAN PASS ([record](docs/history/ACCEPTANCE-24b32ee.md)),
  accepting R1-R3 from the code review of `9627adb`, with a new agent
  session.
- `24b32ee` run 24: stopped in phases 1-3 by the agent, which replaced and
  changed the guide's commands; no product defect (same record).
- `68eece4` run 23: stopped in phase 8, where the guide asked for the backup
  names by hand and the agent filled in an empty one; the guide now reads
  them from the log (same record).
- `9627adb` run 22: CLEAN PASS ([record](docs/history/ACCEPTANCE-9627adb.md)),
  the first run with DR moved to `deploy/dr` apart from the installer (S1),
  and the first clean run with the fixes from runs 20 and 21.
- `1a276fe` run 21: functional pass, not clean
  ([record](docs/history/ACCEPTANCE-1a276fe.md)). `failover` ran once and
  promoted, every tool step passed, but the readiness check before phase 1
  was not logged. The guide now gives it as a fixed line.
- `8e4e492` run 20: functional pass, not clean
  ([record](docs/history/ACCEPTANCE-8e4e492.md)). `REPORT.md` said ALL STEPS
  PASS, but `failover` ran twice: the agent's harness killed the first run
  after it had promoted, and the second run replaced its log. The product
  behaved as designed. A product step now refuses to run twice, `failover`
  runs in the background, and `report` flags a failover that did not promote.
- `4137c5e` run 19: functional pass, not clean
  ([record](docs/history/ACCEPTANCE-4137c5e.md)), the first run with the time
  limits (G6) and the review fixes. `check services` failed once on a Podman
  health-check unit that had failed while a container started, and passed on
  a repeat; it now ignores those units. No product defect.
- `2dbc561` run 18: CLEAN PASS ([record](docs/history/ACCEPTANCE-2dbc561.md)),
  the first run with `app-ops failover` (G1) in phase 6.
- `aeefe4a` run 17: CLEAN PASS ([record](docs/history/ACCEPTANCE-aeefe4a.md)),
  after the CI full-stack job found that Podman 5.7 lets `envFrom` win over
  `env`; `DATABASE_USER` now lives only in `env`.
- `7402641` run 16: CLEAN PASS ([record](docs/history/ACCEPTANCE-7402641.md)).
- `c12444a` run 15: functional pass, not clean
  ([record](docs/history/ACCEPTANCE-c12444a.md)). `REPORT.md` said ALL STEPS
  PASS, and the product passed everything, but the agent silently replaced the
  two `check ports-closed` steps of phase 6 with four narrower
  `check connect` steps. `report` now compares the steps with the guide.
- `5a0b544` run 14: functional pass, not clean
  ([record](docs/history/ACCEPTANCE-5a0b544.md)), the first full run with
  `acceptance.py`: 105 steps, one FAIL. `check replication-tls` ran before the
  standby had reconnected after a reboot and passed on a repeat; the check now
  waits (`c12444a`). No product defect.
- `27f77b1` run 13: BLOCKED in phase 4, step 04-5, on the first full run
  with the tool. `do pin-ssh` passed the public key to the remote shell
  unquoted, so only `ssh-rsa` reached `authorized_keys`. The tool now quotes
  every remote argument. No product defect; no separate record.
- `b9bffbf` quick run 1: QUICK PASS
  ([record](docs/history/QUICK-b9bffbf.md)), the first run with
  `acceptance.py`: 14 steps, all PASS, none repeated, nine minutes. Single-host
  behaviour only; it does not replace a full run.
- `e8e1919` run 12: REPAIRED FUNCTIONAL PASS
  ([record](docs/history/ACCEPTANCE-e8e1919.md)). Every functional gate
  passed again, with one log per phase 10 step. Repair: the agent added the
  phase 3 HTTPS rule without `--permanent`, and a firewalld reload dropped it.
  The logs also show a failed Notes write probe of the agent's own making that
  did not stop the run.
- `8d625a4` run 11: REPAIRED FUNCTIONAL PASS
  ([record](docs/history/ACCEPTANCE-8d625a4.md)). Replication over TLS
  (`ssl = t`, TLSv1.3), the security headers and Notes login work on real VMs.
  Not clean: the quarantine rehearsal was rerun after the firewall had not yet
  applied, phase 10 lacked evidence for three steps, and the draft record had
  a wrong CA fingerprint. The agent guide now waits for firewall changes and
  asks for one log per phase 10 step.
- `d1c04a4` run 10: BLOCKED in phase 3, step 7. The new Content-Security-Policy
  allowed only same-origin requests, so the browser blocked Notes' token
  request to Keycloak's canonical origin (`https://todo.test:8443`) and Notes
  login failed. `connect-src` now names the identity origin. No separate record.
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
boundaries remain unchanged. app-ops (`deploy/dr`, plain SSH) is the only DR
tool; the Ansible playbooks were retired after its CLEAN PASS, and Git history
keeps them. Planned work, its order
and its principles are in the [backlog](docs/BACKLOG.md), with a one-page
[target picture](docs/TARGET-PICTURE.md). Off-host backup, automatic HA and
other IdP adapters are not demonstrated production features.

The [Development journal](docs/history/DEVELOPMENT-JOURNAL.md) preserves earlier
checkpoints; historical next steps are not current instructions.
