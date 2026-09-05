# Development journal

## Goal

Learn rootless Podman by building and deploying a small Todo application.

The project demonstrates Podman, Quadlet, Ansible and offline installation in small, understandable steps.

## Current status

Learning-path follow-up: current LEARNING-GUIDE is Kube-native; the previous
per-container guide is preserved in docs/legacy and included in both bundles.
README lifecycle/API wording is corrected. Frontend now depends on auth.js,
with SDK configuration, PKCE, redirects and refresh in keycloak-adapter.js.
Isolated Chromium adapter tests use a controlled SDK double; they do not prove
integration with a second provider or replace real Keycloak acceptance.
CI runs these browser tests and ShellCheck now includes todo-quarantine.sh.
No VM deployment or acceptance-status change is part of this follow-up.

2026-09-05 final repaired clean-rerun evidence: VM108 (.108, guest
todo-standby) is the writable promoted primary; VM107 (.102, guest
todo-primary) is the read-only rebuilt standby. VM107 retains Proxmox
quarantine with inbound SSH exceptions and outbound TCP5432 only to .108
(plus the documented DHCP/NDP allowances); its onboot remains 0.
Final sequential reboots passed: VM107 boot 5dda5783-842e-4d95-907c-25b74dc2e8fd,
VM108 boot 54d79510-0ea0-437f-8ba2-f31d14b580ad. Fresh cluster-status passed
with active reserved todo_rebuilt_standby slot, streaming and zero lag.
No failed user units; current-primary workload NRestarts=0; containers healthy.
Two Chromium tests passed after the final reboot with TLS verification enabled.
Authenticated marker ID7, `Final DR marker 54d79510`, appeared on rebuilt
standby together with original marker ID2 and both live PITR markers.
Verified base backup: base-20260905T123104Z. Isolated network=none PITR
paused read-only at pitr_20260905T123104Z, contained only the before marker;
live contained before and after. Only disposable restore container/volume were
cleaned up. Post-reboot restore point final_54d79510 archived successfully;
archive failures=0. Backup remains on-VM, not off-host protection.

This is NOT an unchanged-revision acceptance pass. Two quarantine fixes were
installed during the run: accept stopped failed units only with zero MainPID,
zero ControlPID and no running containers, preserving warning/failure evidence;
restore the existing persistent SELinux label after atomic helper replacement.
Rebuild copied and started the standby successfully but then failed because
the assistant omitted become-password prompting for DR tool installation.
The operator completed only the tail with --ask-become-pass and
--start-at-task "Create the Todo DR configuration directory": ok17/2, failed0.
The runner still records rebuild failed; direct cluster-status passed afterward.
Do not rerun destructive rebuild or silently overwrite this failure history.
Runner-state reconciliation and a fresh unchanged-revision acceptance remain open.

Clean rerun d45b257 (2026-09-05): both snapshots restored, SSH keys re-added;
both hosts had no Podman containers, volumes or secrets, enforcing SELinux and
active security services. Both verified packages report the identical clean
revision d45b257f514b25db53a050ca8bb1662997269906. Both preflights passed.
Initial primary install: ok=53 changed=16 failed=0. System-trusted HTTPS and
both Chromium tests passed with TLS exceptions disabled. Authenticated marker
ID 2, Clean d45b257 2026-09-05 before reboot, survived browser reload.
Pre-reboot primary boot ID: 63470ae7-8e65-431c-b273-340afd167f93.
Public CA SHA256: 4a9cb0abf872ddac1898086436fee9c8804e6b57770a58aa00720616a3f8aefb.
Issuer remains https://todo.test:8443/auth/realms/todo. Initial primary reboot
and install repeat are next; standby remains unconfigured. Earlier entries
describe the previous repaired drill, not completed phases of this clean run.

Final DR checkpoint (2026-09-05): rebuild passed (old primary ok=53 changed=14,
current primary ok=34 changed=3, no failures). Authenticated marker ID 7
replicated to rebuilt standby. Final sequential reboots passed: standby boot
9038a38b-f947-4795-99d8-c402469e93c6; current primary boot
7857db69-e863-443c-ac00-129cd41f4da9. Streaming async, active reserved slot,
zero measured lag, read-only database-only standby, healthy writable primary
and archiving. Application NRestarts=0; transient startup healthcheck failure
cleared without manual reset; no failed user units remained. nginx -t passed.
Backup 52M, WAL 161M, 16G free; verified basebackup and all markers survived.
Both browser tests passed with E2E_IGNORE_HTTPS_ERRORS=false after importing
the verified promoted Todo CA into the existing NSS database (TLS CA only,
nickname todo-lab-ca-21166b09). System-trusted HTTPS also passed.
This completes the exercised DR/reboot chain, not a fresh clean-install pass:
the initial standby bootstrap was repaired and artifacts were updated during
the drill. A consolidated clean-revision rerun and short operator automation
remain follow-up work. Older pending-phase notes below are historical.

PITR checkpoint (2026-09-05): backup configuration passed ok=41 changed=7
failed=0. Verified base backup base-20260905T104256Z; restore point
pitr_20260905T104256Z archived at 0/6001F70. Isolated restore reported
Network=none and recovery|paused|read_only=t|t|on. Restore contained only
the before marker; live retained both before/after markers and HTTPS ready.
Archive mode on, timeout 1h, six archived segments, zero archive failures.
The disposable restore container and volume were removed with exact tool
confirmation; base backup and WAL remain. This is not off-host protection.
Backup installer repeat and guarded old-primary rebuild remain pending.
Earlier pending-phase notes below are historical checkpoints.

Promoted-host checkpoint (2026-09-05): hypervisor fencing confirmed stopped
VM 107, onboot=0, disconnected net0 and no matching HA resource. DR preflight
and promotion passed; runner promotion/application stages are completed.
Database is writable, original marker ID 2 survived, and authenticated browser
write created persistent failover marker ID 4. Both Playwright tests passed
(browser certificate exception; system-trusted HTTPS checked separately).
Application repeat: ok=35 changed=0 failed=0. Promoted VM 108 reboot passed:
boot ID 9b35b154-dc84-4825-ae76-29bd94b72b60, all containers healthy,
HTTPS ready, both markers preserved and public CA hash unchanged:
473c9fac865ac9a70806bb6eae1d32900295e0e0fe0e00bdf9d46879718a4c30.
Backup/PITR, old-primary rebuild and final verification remain pending.
VM 107 must remain fenced; never restart it unrestricted after promotion.

Quarantine rehearsal (2026-09-05): Guest Agent READY and STOPPED passed.
With the primary link disconnected, all Todo services and containers stopped.
Installed IPv4/IPv6 quarantine rules were inspected before reconnecting.
Fresh management SSH passed; fresh outbound SSH and inbound TCP 8443 to a
confirmed temporary listener timed out. The listener exited automatically.
Follow-up: disconnected shutdown/start and Guest Agent STOPPED passed with
new boot ID 835b0d1d-0d9e-4ae4-a993-64c21738a26b. Fresh IPv6 link-local SSH
connections timed out in both directions with quarantine enabled and connected
in both directions after disabling it. These rehearsal checks now pass;
fencing, promotion, application failover, backup/PITR and rebuild still remain.
No promotion or reseeding occurred. VM filtering was disabled, services
restarted, all containers became healthy, and read-only standby streaming
resumed with zero measured lag. Trusted HTTPS readiness and marker ID 2 passed.
Earlier preparation notes below are historical.

Quarantine preparation (2026-09-05): DR tool installation and its repeat run
passed (`ok=15 changed=0 failed=0` on the repeat), and installed tool status
confirmed healthy read-only standby, reachable primary and zero apply lag.
Fencing is paused at the operator's request until old-primary reintroduction
can be done without guest-console typing. A root-owned Guest Agent stop helper,
its pre-install playbook and PROXMOX-QUARANTINE.md are prepared and locally
tested (145 tests plus shell lint and Ansible syntax). Nothing has been
installed or stopped by this helper yet. Proxmox version, firewall enable
state/backend and actual isolation remain unverified. The intended route is
disconnected guest links, Guest Agent stop, then restricted SSH after STOPPED.
Do not fence primary until the host-specific quarantine rehearsal passes.

DR preparation checkpoint (2026-09-05): standby reboot passed with boot ID
`c11dd2ee-309a-468c-b8fa-3f8a8e9902c0`, read-only recovery, marker ID 2,
matching receive/replay LSNs and zero measured streaming lag. Both hosts then
received verified clean packages from `494666ad7db7eda60d6816fbf1dbf1a1c35a648b`.
DR tool installation stopped when its immediate active-trust check did not
find the newly added paths. A subsequent operator dump showed both exact
filedb entries with matching sizes and hashes; the files were installed and
the tool help ran, but `todo-dr.json` was not yet written. The trust role now
waits with bounded retries after both controller and target reload requests,
requiring exact canonical paths, sizes and hashes before proceeding. Simulated
delayed reload and stale-hash failures are exercised with real Ansible.
Next: rerun the corrected installer with interactive sudo, verify status and
installer idempotence. Both hosts must remain running until fencing is agreed.

Active acceptance checkpoint (2026-09-05): both VMs hold verified clean
artifacts from `093557dda4c880e18c5cdb91a048d6abbe4d7878`. Primary `.102`
was upgraded through the packaged installer: redirect reconciliation reported
`changed=1`; the repeat deploy reported `ok=40 changed=0 failed=0`. Both
Playwright flows and system-trusted HTTPS readiness passed. Authenticated
marker `Kube 093557d 2026-09-05 before reboot` (Todo ID 2) remains present
after browser reload and through the public HTTPS API.
Pre-reboot boot ID is `fef053d0-b1d2-4842-8a18-de594ecccedf`; public CA file
SHA-256 is `2f7473b69d78424fe2a0dea9083cee7ad04a36f5aab0161f0c48ea67abe5ab2f`.
Primary reboot passed with new boot ID `4f6d5e3c-e1d7-41f0-8b89-2a5341877a65`,
unchanged CA, services, HTTPS and marker. Reinstall passed `changed=0 failed=0`.
Primary-to-standby SSH now passes with strict host-key checking. The operator
opened primary TCP 5432 only from `.108`; initial standby preflight passed.

Bootstrap then failed at standby health after completing base backup and
secret synchronization: the last helper's private SELinux MCS label prevented
the Kube container from reading PGDATA. Primary stayed healthy. Standby was
stopped, its exact Podman volume path and lack of running consumers checked,
and only its data volume relabeled to `container_file_t:s0`. No volume was
deleted and no base backup was repeated. Standby then reported `t|on`, matching
receive/replay LSNs, and marker ID 2; primary reported `streaming|async|0` and
an active reserved slot. Source bootstrap/rebuild now use `:z` on their final
configuration helper, preserving all fencing and data-removal checks.
Next: reboot only standby and repeat replication/marker assertions. Its
pre-reboot boot ID is `a7874d35-f01b-4200-898b-ed74c2acf788`.
Final clean acceptance remains pending. This is manually repaired bootstrap
and upgrade evidence, not an uninterrupted clean-install PASS.

Second-round finding (2026-09-05): `4476071` passed clean-host checks,
installation, external 8443 binding, system-trusted HTTPS, readiness and issuer
checks after a client-scoped firewall rule and new CA trust were installed.
The public Playwright flow failed with `Invalid parameter: redirect_uri`:
the imported realm allowed only localhost. Clean deploy now reconciles the
frontend redirect/origin with the configured HTTPS issuer using the existing
Podman administrator secret and suppressed token-bearing tasks. It preserves
PKCE and audience settings and only reports a change when reconciliation is
needed. VM IP configuration is mapped in LAB-ACCEPTANCE.md. Testing an upgrade
on the current primary does not replace the final clean-install gate.

Acceptance findings (2026-09-05): the manually reset VM pair passed clean-state
checks and received clean artifacts from `b3b479efa6a7c065da2a6efdbe8ca68c6aea5927`.
Initial installation passed (`ok=46 changed=15 failed=0`), as did the three
Kube services, local health/readiness, nginx and the stable issuer. Client DNS
and CA trust passed, but external HTTPS failed because 8443 was published only
on localhost. Firewall state remains unverified (interactive sudo required).
The installer now accepts an explicit host IPv4 publish address; the runbook
includes a client-scoped firewall rule and repeats the address on redeploy.
Separately, missing Helm emptied `app.yaml` during the first build attempt;
the file was restored before packaging. Rendering now completes all manifests
in temporary storage before replacing output files. Fresh clean-artifact VM
acceptance remains required. Browser, reboot, replication and DR gates have not
passed in this round. Reset and hypervisor fencing remain manual operations.

The rootless Podman, Quadlet, nginx, Ansible and offline-installation reference
is complete. A full clean Oracle Linux 9.8 acceptance drill passed from initial
deployment through standby bootstrap, fencing, promotion, application failover,
backup/PITR, destructive re-seed and reboot of both final database nodes.

The reusable procedure and pass criteria now live in
[docs/LAB-ACCEPTANCE.md](docs/LAB-ACCEPTANCE.md). This file remains the
development journal: it records why the current design exists and preserves
historical experiments that a new operator does not need for normal use.

The final deployment model has three explicit Kube workload boundaries: a
grouped `todo-app` pod containing a migration init container, backend and
frontend; an independent Keycloak pod; and an independent PostgreSQL pod.
Clean deploy and the complete standby/promotion/backup/rebuild code path now use
that model directly. Static tests, Ansible syntax/lint and bounded
migration-connection retry pass; the new end-to-end path still requires the
clean Oracle Linux VM, reboot, replication and full DR acceptance gates.

The final pre-VM review corrected the explicit Podman container-name contract,
the installed DR runner project-root discovery, Python 3.9 CI compatibility,
Kube service names in the acceptance runbook and the fixed `todo.test:8443`
promotion identity. The local CI-equivalent gate passes 132 tests, Ruff,
ShellCheck, Ansible lint/syntax, Helm render drift and operations-package
verification; the hosted workflow must still confirm the pushed checkpoint.

## Operator-side DR automation - local implementation

`scripts/lab_dr_acceptance.py` now implements the first `reset-check` profile:
validated Proxmox VM/snapshot allowlists, explicit destructive confirmation,
resumable private operator state, snapshot rollback, QGA/SSH readiness and a
read-only clean Oracle Linux/Podman preflight. Twelve focused unit tests and the
full local regression suite pass. The controller has not been run against the
lab yet. Install, replication, fencing, promotion, quarantine, rebuild and the
full profiles remain future stages; the manual runbook remains authoritative.

## Next session checkpoint - 2026-09-04

Work from the pushed head of `feature/podman-kube`. The working tree must be
clean before packages are built. No version of `lab_dr_acceptance.py` has yet
changed a VM; only its local configuration, CLI, state and command construction
have been tested.

Tomorrow's first boundary is the new `reset-check` profile:

1. Copy `lab-dr.example.toml` to the ignored `lab-dr.local.toml`.
2. Set the exact Proxmox SSH target, SSH identities and clean snapshot names.
3. Run `validate --local-only`, `plan` and the read-only remote `validate`.
4. Review VM IDs 107/108 and then explicitly authorize `run --confirm-reset
   107:108`.
5. Require a PASS report for both snapshot reset and clean-host preflight.

Snapshot rollback deliberately destroys the currently promoted lab databases,
backup archive and runner state. Once reset is authorized, the older paused
checkpoint below is historical evidence only and must not be resumed.

Continue from the clean pair in this order, following
`docs/LAB-ACCEPTANCE.md` wherever the controller does not yet implement a
stage:

1. Build both packages from the same clean revision and verify their metadata.
2. Install the final Kube primary, run trusted HTTPS/browser checks and create a
   run-specific pre-failover marker.
3. Bootstrap the standby, require healthy streaming/zero lag and verify the
   marker on the read-only standby.
4. Fence the initial primary at Proxmox, independently prove power-off and
   network unreachability, then promote with the existing guarded DR runner.
5. Restore the application, verify the pre-failover marker and create a
   post-promotion marker.
6. Configure backup/WAL archiving and pass base-backup plus isolated named-point
   PITR without modifying the live database.
7. Reintroduce the old primary only through hypervisor quarantine. Use QGA to
   stop its Todo services before opening management traffic, then run guarded
   standby rebuild and verify both markers on the read-only rebuilt standby.
8. Require the final Kube boundaries `todo-app`, `todo-keycloak` and
   `todo-postgres` throughout the recovered topology.
9. Reboot standby and primary sequentially; repeat application, TLS, Keycloak,
   persistence, replication, archive and marker assertions.
10. Record final evidence. Only then may legacy transition/rollback tooling be
    retired; it is not exercised as part of the normal deployment path.

Only after every gate passes may the grouped model be marked accepted and the
legacy Quadlet retirement plan in `quadlet/QUADLET-REFERENCE.md` begin. Any
failure must be recorded before repair; destructive stages with unknown
outcomes are never blindly retried.

## Previous paused DR runner validation - 2026-09-03

Before a clean reset, this was the resumable checkpoint. Do not use it after
the snapshot pair has been restored:

- Source artifact revision is `def82c88febe35b65406cb9899de38832e63cdff`; both
  offline and operations packages reported `source_state=clean`.
- `todo-standby` (`192.168.0.108`, Proxmox VM 108) is the promoted writable
  primary and runs the per-container application. Health, readiness, trusted
  HTTPS, issuer, authenticated writes, idempotence and reboot passed.
- The nginx demo CA SHA-256 is
  `6caf47b1fd085a3de4bd98483bc9bc5e74b1699da70ed651aeb1c3c7ac9d4fae`.
- Persistent rows include the M12, M13, M14 and both M15 restore-point markers.
- WAL archiving is healthy with `archive_timeout=1h`, zero failures and verified
  base backup `base-20260903T174645Z`. Named-point PITR at
  `m15_before_after` passed; its disposable container and volume were removed.
- DR runner state on `.108` has `promotion` and `application` completed; rebuild
  and both Kube migrations remain pending. Keep that state file.
- `todo-primary` (`192.168.0.102`, Proxmox VM 107) is the fenced, powered-off
  divergent old primary. Do not boot it with unrestricted workload traffic.
- Proxmox quarantine rules and the `.108` inbound replication rule for `.102`
  were discussed but not confirmed. Verify them before booting VM 107.
- Do not run `rebuild` until all old-host Todo services are stopped and the
  runner read-only rebuild preflight passes.

Known automation gaps found during this run:

- The runner requires the M15 backup boundary before rebuild but has no
  backup/PITR stage. The full M15 workflow was therefore completed manually.
- Repeated drills need a coordinated DR-ready snapshot pair after deployment,
  replication, inventories, SSH keys and trust setup; an OS-clean snapshot makes
  every drill unnecessarily long.
- The console-based quarantine instructions are impractical in this lab. Replace
  them with a tested Proxmox firewall procedure or separately authorized API
  automation while retaining external fencing.
- Recovery-direction SSH and exact `fapolicyd` trust for `todo_dr_run.py` must be
  pre-staged and checked by a future runner preparation command.

## Clean nginx acceptance checkpoint - 2026-09-01

- Both offline artifacts were built from clean revision
  `d8506f75721f92b704eec0669bf2dda36ff18bdb`, and their extracted `VERSION`
  metadata matched the archives on both hosts.
- The initial nginx deployment passed trusted HTTPS, two Playwright tests,
  persistent authenticated data, reboot and idempotent reinstall.
- Standby bootstrap passed with protected Podman-secret synchronization,
  `streaming|async`, an active usable slot and zero measured lag.
- The local DR tool passed exact-file `fapolicyd` trust, reboot, zero-lag
  fencing preflight and controlled promotion to `f|off`.
- Application failover loaded only the staged offline images, retained stable
  issuer `https://todo.test:8443/auth/realms/todo`, passed authenticated write,
  idempotence, reboot and unchanged nginx CA identity.
- Base backup `base-20260901T160954Z` passed `pg_verifybackup`. Named-point
  PITR exposed only the before-row in the isolated read-only restore while the
  live database retained both rows.
- The divergent old primary was quarantined and re-seeded only after secret
  equality, TCP reachability and authenticated replication checks.
- Final state passed reboot of rebuilt standby followed by current primary:
  `.108` reported `f|off|on|1h`, healthy archiving and all application
  services; `.102` reported `t|on`, matching receive/replay LSNs and zero
  measured streaming lag.
- Final backup use was 52 MiB base plus 161 MiB WAL with 16 GiB free. The
  one-hour archive timeout prevented recurrence of the earlier low-traffic
  capacity incident.

## Secure operations hardening checkpoint — 2026-08-31

- The optional Vault provisioning path was removed. The demo deliberately uses
  Podman secrets only and treats simultaneous loss of both database nodes as
  outside scope.
- All operational secret reads use Podman `secret inspect --showsecret`;
  value-bearing reads and mismatch assertions use `no_log`.
- Both the M12 image bundle and the operations package record their source Git
  revision and clean/dirty build state for offline traceability.
- CI now builds the nginx frontend image, runs its non-root OpenSSL certificate
  bootstrap, parses the real configuration with `nginx -t`, verifies the
  `todo.test` SAN and chain, and checks private-key modes. The same smoke passed
  locally with Docker.
- The final from-zero nginx drill was completed on 2026-09-01; its reusable procedure and evidence moved to docs/LAB-ACCEPTANCE.md.

## Live nginx migration checkpoint — 2026-08-31

- The verified offline frontend archive replaced the Caddy-tagged image with an
  image labelled `io.todo.proxy=nginx`. M14 rejected stale proxy images before
  changing runtime state.
- M14 removed obsolete Caddy runtime files, installed the nginx configuration
  and persistent TLS volume, and completed with `failed=0`.
- Local health, readiness, public API and Keycloak discovery passed through
  nginx. The external issuer remained
  `https://todo.test:8443/auth/realms/todo`.
- The new nginx demo root was installed on the Ubuntu test client. Replacing the
  old CA path required `update-ca-certificates --fresh`; direct system-trust HTTPS
  then passed without `--cacert` or an insecure bypass.
- Browser login and authenticated Todo creation passed. A repeat M14 deployment
  reported `changed=0`.
- After reboot all four application services were active, `nginx -t` and HTTPS
  readiness passed, PostgreSQL remained `f|off|on|1h`, and the M16 rebuilt
  standby resumed `streaming|async` in read-only recovery.

## Live clean-deployment checkpoint — 2026-08-29–30

Resume from this exact state:

- `todo-primary` is `192.168.0.102`; `todo-standby` is `192.168.0.108`.
- Both are independently installed Oracle Linux 9.8 VMs with 4 GiB RAM,
  SELinux enforcing, active `fapolicyd`, rootless Podman, user lingering and
  clean Proxmox baseline snapshots.
- Primary-to-standby SSH uses primary's RSA key and passes with
  `BatchMode=yes`.
- A clean M12 offline install passed HTTPS, Keycloak login, authenticated Todo
  creation, reboot and an idempotent rerun with `changed=0`.
- The database contains `M12 clean deployment test` and
  `M13 streaming replication test`.
- M13 bootstrap passed from scratch. Primary reports the physical
  `todo_standby` slot as active and `streaming|async` with zero measured lag;
  standby reports recovery mode and matching receive/replay LSNs.
- The standby resumed recovery and streaming after reboot, and both Todo rows
  were verified directly in its read-only database.
- M13.5 was installed separately after replication. On standby,
  `todo_dr.py status` reports an active healthy standby, read-only database,
  zero local apply lag and reachable primary `192.168.0.102:5432`.
- The old writable database on `todo-primary` was quarantined and permanently
  destroyed by M16. That host is now the read-only database-only standby and
  may start at boot; it must not be treated as primary based on its hostname.
- `todo-standby` was promoted in approximately two seconds and verified as
  `f|off` with a rolled-back writable transaction.
- M14 deployed the full application tier on the promoted host without database
  bootstrap, migrations or grants. `todo.test` now maps to `192.168.0.108`.
- HTTPS health/readiness, the stable Keycloak issuer, the replicated Keycloak
  user and an authenticated `M14 clean failover test` write all passed.
- A second M14 deployment completed with `changed=0`; after reboot all four
  services were active, PostgreSQL remained writable and client HTTPS checks
  passed.
- M15 enabled continuous WAL archiving on the promoted host and completed an
  idempotent second deployment with `changed=0`.
- Verified base backup `base-20260830T062555Z` completed in approximately two
  seconds and contains a valid `backup_manifest`.
- Named restore point `m15_before_after` was archived at `0/7000220`. The
  isolated read-only PITR database contained `M15 before restore point` but not
  `M15 after restore point`; the writable live database retained both rows.
- Exact-confirmation cleanup removed the disposable restore container and
  volume. After reboot, all services, the base backup, application readiness
  and newly generated WAL archiving were verified again.

Exact current operating state:

1. `todo-standby` (`192.168.0.108`) remains the writable primary and runs
   the application plus M15 backup/WAL archiving.
2. `todo-primary` (`192.168.0.102`) is the rebuilt read-only standby and
   runs only PostgreSQL.
3. No failback is required. A later role reversal would be a separately planned
   switchover with fencing, catch-up and rollback controls.

New issues found during this drill:

- The original M15 `archive_timeout=60s` produced 1,001 mostly empty 16 MiB
  archive segments and filled the 18 GiB rootless Podman filesystem during an
  otherwise nearly idle soak test. A newly verified base backup was retained,
  older WAL was expired with `pg_archivecleanup`, and service readiness
  recovered. M15 now defaults to one hour, exposes the timeout in backup status
  and retains explicit operator control over destructive recovery-history
  expiration.

- M16 initially failed Ansible module transfer on both hosts because pipelining
  was not consistently configured. Active `fapolicyd` denied transient
  `AnsiballZ_*.py` files. Pipelining is now a project-level `ansible.cfg`
  default for both local and SSH execution, rather than repeated inventory data.
- The M13.5 drill exposed that embedding Python in a role YAML file caused
  `fapolicyd` to classify the task file as executable content. The installer now
  uses RPM-managed shell/core utilities through pipelined stdin, so only the
  exact Python source and deployed operational files need custom trust.
- Standby's deployed `/opt/todo/bin/todo_dr.py` and `todo-dr.json` are recorded
  in its exact `todo-dr` trust file, and the local status command succeeds.
- The clean drill exposed that the M12 builder included top-level M13-M16
  playbooks without their roles. The builder is now restricted to M12's usable
  `deploy.yml`, `uninstall.yml`, inventory and requirements files.
- When replacing both example addresses on one inventory line with `sed`, use a
  global replacement or update `todo_node_address` explicitly; the first test
  correctly failed read-only preflight when that second value remained
  `192.0.2.11`.

## Completed

The list below preserves historical milestones, including the former Caddy implementation. The accepted runtime is nginx.


- `GET /health` returns `{"status": "ok"}`.
- PostgreSQL runs in a rootless Podman container with a named volume.
- The Todo API supports create, read, update and delete operations.
- The frontend uses plain HTML, CSS and JavaScript.
- The frontend can create, complete and delete Todos.
- FastAPI serves the frontend.
- The README contains local development instructions.
- PostgreSQL data was verified to survive a container stop and restart.
- Versioned SQL migrations support status, upgrade and single-step rollback.
- Migration upgrade and rollback were verified against a disposable test database.
- Nineteen automated backend tests cover health, readiness, frontend serving, CRUD, validation, migrations and positive and negative JWT validation.
- Tests were verified against an isolated, automatically removed PostgreSQL test container.
- Backend and frontend images build successfully and pass isolated smoke tests as UID 1000.
- PostgreSQL, backend and frontend run manually on the rootless `todo-network`.
- Backend reaches PostgreSQL through Podman DNS using the `todo-postgres` name.
- Database persistence was verified across a PostgreSQL container stop and start.
- Container logs, inspect data, routes and lifecycle were used to diagnose and repair a Netavark namespace issue.
- Liveness (`/health`) and database readiness (`/ready`) are separate endpoints.
- Caddy provides one browser-facing endpoint and routes frontend and API traffic by path.
- Full Todo CRUD was verified through Caddy.
- One Chromium E2E test verifies the complete browser Todo flow through Caddy.
- Rootless Quadlet definitions describe the network, persistent volume, PostgreSQL, migration, backend and frontend.
- Quadlet dependencies enforce PostgreSQL health, role setup, completed migrations, final runtime grants, backend startup and frontend startup in that order.
- The Quadlet lifecycle and browser flow were verified through user systemd.
- PostgreSQL, migrations and the backend receive the database password from a file-mounted rootless Podman secret.
- The secret-based deployment passed health, readiness, API and Chromium E2E checks.
- A small localhost Ansible playbook builds the images, installs Quadlet files, creates the Podman secret when missing, starts the services and verifies health and readiness.
- A second Ansible run completed with `changed=0`, confirming idempotence.
- The Ansible-deployed application passed the Chromium E2E test.
- An offline tar.gz bundle contains OCI image archives, deployment files, a target preflight check, an installer and SHA-256 checksums.
- Oracle Linux 9 offline installation was verified with SELinux enforcing, active `fapolicyd` and RPM-managed `ansible-core`.
- Offline installation was verified after removing all three local images and blocking registry and package-index access with invalid proxies.
- PostgreSQL data survived the offline reinstall.
- Caddy served locally issued HTTPS on `https://localhost:8443` while retaining HTTP on port 8080 for development.
- Caddy's internal CA persists in a dedicated rootless Podman volume.
- TLS hostname, certificate chain, health, readiness and Chromium E2E behavior were verified.
- M1 through M10 are complete.
- Keycloak runs behind Caddy and imports a minimal `todo` realm with a public SPA client using PKCE S256.
- Todo reads remain public, while create, update and delete operations require a valid Keycloak access token.
- Keycloak uses the existing PostgreSQL service with a separate bootstrap-owned database schema.
- The temporary Keycloak bootstrap-administrator password is held in a rootless Podman secret and no users or passwords are committed.
- Public-read and authenticated CRUD browser flows were verified through Keycloak.
- A helper provisions the complete `testuser` account and runs both E2E flows without storing test passwords.
- M11 is complete.
- Direct application dependencies and base-image patch versions are pinned.
- An explicit `refresh_images=true` mode separates idempotent reuse from security refresh.
- PostgreSQL runtime access is split into non-superuser migration, backend and Keycloak roles with independent secrets.
- The migration, backend and Keycloak roles cannot inherit one another's database privileges.
- Fresh-install and upgraded-database checks verified that the Todo roles cannot access Keycloak data and the Keycloak role cannot access Todo data.
- An idempotent role-setup service upgraded the existing volume without losing Todo or Keycloak data.
- Backend, frontend and Keycloak run with no new privileges, no effective Linux capabilities and explicit PID limits.
- JWKS discovery is cached across requests and negative JWT tests cover issuer, audience, expiry, signature and algorithm.
- Build context excludes environment files, virtualenvs, archives, private keys and distribution artifacts.
- M12 backend tests, role setup, migrations, service startup, readiness, discovery and Ansible `changed=0` were verified.
- Both the public and authenticated M12 browser flows were verified after the least-privilege upgrade.
- A clean-install CI job verifies dependency installation, role bootstrap, migrations and backend tests against an empty PostgreSQL database.
- The clean role-setup and migration sequence was verified against an empty disposable PostgreSQL database.
- Default uninstall/reinstall was verified to preserve Todo data, Keycloak data and their existing credentials.
- M13 preflight verified unique hostnames, machine IDs and LAN addresses on two Oracle Linux 9.8 VMs.
- A dedicated `todo_replicator` role and Podman secret provide asynchronous PostgreSQL streaming replication.
- Primary-to-standby secret synchronization was verified through Ansible memory and SSH without plaintext secret files.
- A streamed `pg_basebackup`, physical replication slot and rootless standby Quadlet were verified with SELinux enforcing.
- Primary reports `todo_standby` as `streaming|async` with zero measured lag; standby reports recovery mode with matching receive and replay LSNs.
- A Todo written through the primary application was verified directly in the read-only standby database.
- Standby automatic boot, recovery re-entry and resumed zero-lag streaming were verified after a full VM reboot.
- The cloned standby required a one-time `podman system renumber` with all user Podman processes stopped to repair inherited runtime lock allocation; no volume data was removed.
- A standard-library Python DR tool implements local standby status, fail-safe fencing preflight and verified PostgreSQL promotion.
- Ten isolated unit tests verify successful promotion and rejection of unsafe fencing, reachability, lag and confirmation states.
- A live Oracle Linux 9.8 drill fenced primary at the VM layer, passed the local preflight with zero apply lag, promoted standby in approximately two seconds and verified `f|off` plus a rolled-back Todo write.
- The old primary remains fenced after promotion; it must not rejoin without being rebuilt as a replica of the promoted database.
- M14 loaded the staged backend, frontend and Keycloak images on promoted standby without running database bootstrap, migrations or grants.
- `https://todo.test:8443` served a stable Keycloak issuer, health, readiness and replicated public Todo data through a firewall rule restricted to the test laptop.
- Authenticated browser login created `M14 browser failover test` through the promoted application stack.
- A repeat M14 Ansible deployment completed with `changed=0`.
- After reboot, PostgreSQL, backend, Keycloak and Caddy all returned `active`; PostgreSQL remained writable with `f|off`, and HTTPS readiness plus Todo data were verified from the laptop.
- M15 configured `archive_mode=on` with a non-overwriting archive command and a separate `todo-postgres-backup` volume on the promoted Oracle Linux 9.8 host.
- A streamed physical base backup completed in approximately three seconds and passed `pg_verifybackup` with a SHA-256 backup manifest.
- A named-restore-point drill restored into the network-isolated `todo-postgres-restore` container without targeting or modifying the live data volume.
- The restored database contained `M15 before restore point` but excluded `M15 after restore point`; the writable live database retained both rows.
- Exact-confirmation cleanup removed only the disposable restore container and volume while preserving the base backup, WAL archive and live data.
- After a full VM reboot, PostgreSQL remained `f|off|on`, all four application services were active, the base backup persisted, WAL archiving resumed with zero failures, and the M15 playbook completed with `changed=0`.
- A longer soak test demonstrated that a 60-second archive timeout could fill the 18 GiB rootless Podman filesystem with mostly empty WAL segments. The demo now uses a one-hour timeout; off-host transfer, explicit retention and capacity alerts remain production requirements.
- M16 quarantined the old primary, removed its divergent database and application Quadlets, streamed a fresh base backup, and restored it as the database-only standby.
- An authenticated `M16 restored redundancy test` write reached the rebuilt standby with zero lag; the standby reported `t|on` and only PostgreSQL active.
- After reboot of both final nodes, current primary remained `f|off|on` with application readiness, persistent backup and zero archive failures; the rebuilt standby resumed `streaming|async` with matching receive/replay LSNs and an active usable slot.

## Decisions

- Implement and understand the application before containerizing it.
- Use rootless Podman.
- Run containers manually before introducing Quadlet and Ansible.
- Prefer simple, pedagogical solutions over abstraction.
- Keep dependencies and Bash scripts minimal.
- Run PostgreSQL in a rootless Podman container during M2 instead of installing it on the host.
- Keep FastAPI running locally through M2; containerize it in M3.
- Add Keycloak last.
- Keep liveness and readiness checks separate.
- Use one nginx-based frontend container to serve static files and proxy backend routes.
- Keep Playwright and Chromium as separate test-only dependencies.
- Mount the database password as a file from rootless Podman secret storage.
- Keep `DATABASE_URL` for local development and tests; containers use separate non-secret settings plus `DATABASE_PASSWORD_FILE`.
- Enable only grouped todo-app.kube; systemd starts the independent Keycloak and PostgreSQL Kube workloads.
- Use Helm only to render Podman-compatible YAML; use direct podman kube play in development and .kube Quadlets in production.
- Require the tested Podman 5.8.2 Kube feature set and keep resource limits in workload YAML.
- Start Ansible with one localhost playbook and only `ansible-core`; add remote deployment structure only when it is needed.
- Keep secret prompting conditional so repeat deployments remain non-interactive.
- Treat Podman, RPM/deb-managed `ansible-core`, user systemd and basic archive/checksum tools as offline target prerequisites.
- Use the operating system's trusted Ansible package on hardened targets instead of executing a bundled Python runtime from a user-writable directory.
- Build platform-specific offline bundles on a machine compatible with the target.
- Verify every bundled artifact with SHA-256 before installation.
- Use a container-local OpenSSL demo CA for offline-compatible local HTTPS without automatically modifying host trust.
- Persist the local demo CA material in a dedicated volume and expose only its public root certificate for explicit trust.
- Never commit secrets.
- Keep authentication authorization simple: all authenticated users may write all Todos; per-user ownership is outside this demo.
- Keep browser tokens in memory and use Authorization Code with PKCE S256 for the public frontend client.
- Treat migration 002 as a pre-baseline correction: Keycloak schema lifecycle moved to bootstrap, and applied migrations are immutable from M12 onward.
- Retain Keycloak's temporary bootstrap administrator only for repeatable localhost administration and E2E setup.
- Keep Ansible responsible for host configuration and desired state.
- Use small, explicit Python tools for replication checks, promotion and failover workflows; do not build a second configuration-management framework.
- Provision identical releases and database credentials to primary and standby from one controlled source.
- Use asynchronous streaming replication with a 30-second operational RPO target; this is not a guaranteed bound after abrupt primary loss.
- Target database write availability within a 15-minute RTO.
- Require fencing of the old primary before promotion to avoid split-brain.
- Treat replication as availability protection, not as a replacement for backup and point-in-time recovery.

## Local development

From the project root:

```bash
source backend/.venv/bin/activate
export DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD"
uvicorn backend.main:app --reload
```

From `backend/`:

```bash
source .venv/bin/activate
export DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD"
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000> in a browser.

## Roadmap

- M1: HTML/JavaScript + FastAPI with hardcoded Todo — completed
- M2: PostgreSQL + Todo CRUD — completed
- M2.5: Versioned SQL migrations — completed
- M2.6: Automated smoke and integration tests — completed
- M3: Containerfiles for backend and frontend — completed
- M4: Run manually with rootless Podman — completed
- M4.5: Troubleshooting and lifecycle — completed
- M5: Caddy reverse proxy — completed
- M5.5: End-to-end browser tests with Playwright for Python — completed
- M6: Quadlet and systemd — completed
- M7: Podman secrets — completed
- M8: Ansible deployment — completed
- M9: Offline installation from a `tar.gz` bundle — completed
- M10: HTTPS — completed
- M11: Keycloak authentication — completed
- M12: Security baseline and least privilege — completed
- M13: Ansible-provisioned PostgreSQL primary/standby with a dedicated replication role — completed
- M13.5: Small Python tools for replication status and controlled promotion — completed
- M14: Full application disaster recovery with stable service hostname, shared deployment state and smoke tests — completed
- M15: PostgreSQL backup, WAL archive and point-in-time recovery — completed
- M16: Rebuild the old primary as a new standby after failover — completed

## Next step

Build fresh offline and operations packages from this revision, then run the
full VM acceptance in order: clean Kube install, idempotent rerun, cold
reboot/persistence, standby bootstrap, replication marker, hypervisor fencing,
promotion, grouped promoted application, backup/PITR, quarantined old-primary
Kube rebuild, sequential reboot and final DR verification.

The active code path no longer installs legacy `.container` units.
`todo_dr_run.py` now has only promotion, application, rebuild and verification
stages. Exact-file trust and installation for DR/backup tools are centralized
under `/opt/todo/bin` by Ansible; acceptance must keep SELinux enforcing,
`fapolicyd` active and firewalld active.

The per-container implementation remains recoverable through
`quadlet-reference-v1`. Transition/rollback roles and their templates remain
temporarily as evidence because the retirement gate explicitly requires the
pending full Kube VM acceptance.

Planned switchover/failback remains separate from restoring redundancy.
