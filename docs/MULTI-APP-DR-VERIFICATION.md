# Phased multi-app DR verification

This record tracks the dedicated DR generalization after the
[single-host prerequisite](MULTI-APP-VERIFICATION.md). It is not a complete
multi-app DR acceptance verdict. The existing Todo acceptance VMs are untouched.

## Isolated targets

Two new QEMU/KVM overlays, `notes-dr-primary` and `notes-dr-standby`, use a
private socket-connected virtual network, 192.0.2.50/24 and 192.0.2.51/24.
These addresses exist only on that virtual segment. SSH from the development
host uses localhost ports 22102 and 22103. Both guests run Fedora 44,
Podman 5.8.1 rootless, 4 GiB RAM, SELinux enforcing and firewalld. Their machine
IDs differ. The original disposable single-host disk is an offline backing
image and must not be booted or modified while these overlays use it.

## Checkpoint 1: Python replication mechanics, Todo only

The Ansible roles and DR operator tool were unchanged for this checkpoint.
`apps.REPLICATED_APPS` contained only Todo. Direct calls exercised
`configure_primary`, `bootstrap_standby`, `status` and `promote` on the guests.

Observed on 23 September 2026:

- Primary configuration returned changed on first use and unchanged on repeat;
  existing credential authentication was tested without logging its value.
- The standby was initialized from the rendered data PVC and a real streamed
  `pg_basebackup`, using its dedicated secret and `todo_standby` physical slot.
- The standby was healthy, read-only and caught up. Primary reported
  `todo_standby|streaming|async|0`.
- The row `todo-only-replication-port-checkpoint` replicated to standby.
- A repeated initial bootstrap refused the existing data volume without mutation.
- Primary was powered off; its QEMU process was confirmed absent before promotion.
- The existing Todo DR fencing, hostname, health and lag preflight passed on
  standby. Python promotion produced a writable database, retained the marker
  and allowed a rolled-back write probe.
- All 144 repository tests and installer Ruff checks passed.

An initial promotion harness invocation lacked the old operator script in the
guest and failed at Python import, before any mutation. After staging that
unchanged script, the full preflight was rerun and promotion succeeded. Original
and repaired outputs are retained outside Git under `/tmp/notes-dr-vms/`.
This development checkpoint did not exercise fapolicyd (not yet active),
backup/PITR or reseeding and does not accept Notes DR.

## Checkpoint 2: Todo-only Ansible replication bridge

Fresh disposable overlays were used for the bridge run; the checkpoint-1 disks
were retained offline. Both new guests had active fapolicyd and firewalld,
SELinux enforcing and distinct machine IDs. The actual operations archive was
extracted and Ansible ran on `notes-dr-primary` over verified SSH to standby.

The migrated `postgres_primary` and `postgres_standby` roles passed the real
`bootstrap-standby.yml` playbook. The recap had zero failures/unreachable hosts.
A new `todo-ansible-bridge-marker` row appeared on the read-only standby;
primary reported `todo_standby|streaming|async|0|t|reserved`. Repeating the primary
role reported `ok=83 changed=0 failed=0`; PostgreSQL restart was skipped.

The first read-only preflight caught the test inventory inheriting the lab's
`gunstein` SSH user from group_vars. The private inventory was corrected with
host-level `ansible_user=notes` and `todo_user_home=/home/notes`, then preflight
ran again through the bootstrap playbook. No database mutation preceded that
identity/SSH correction. All original outputs remain in `/tmp/notes-dr-vms/`.

All 147 repository tests passed with Ansible 2.14 and 2.20. The bridge tests run
real Ansible/Python against inert runtime commands, verify unchanged primary
repeats, and require existing-volume bootstrap refusal without a second base
backup. Canonical Helm PVC comparison, isolated package execution, all-playbook
syntax checks, Ruff and Ansible lint passed. Notes was still excluded from the
replication registry throughout this checkpoint.

## Checkpoint 3: group promotion with only Todo enabled

The generalized operator tool was installed on the real standby through the
unchanged exact-trust mechanism, with fapolicyd active. Its configuration
explicitly selected `applications: [todo]`.

A live negative test deliberately supplied fencing text while primary was still
reachable. Preflight refused it, the database remained read-only, and no
promotion decision record was created. Primary was then powered off and its
QEMU process independently confirmed absent. The new all-app preflight and
promotion passed for Todo; `todo-ansible-bridge-marker` survived and a write
probe succeeded before rollback. The durable decision file contained
`state=complete`, `applications=[todo]`, `completed=[todo]`, mode 0600.

The full suite passed 156 tests. Added tests cover the last app having lag,
missing LSNs, wrong role, inactive service or unhealthy container; a reachable
second primary port; inability to persist the decision; competing promotion
processes; and failure after the first database is promoted. No database is
promoted before the complete preflight passes. A failed/partial decision blocks
blind retry. This is a fail-closed group gate, not a claim that PostgreSQL can
atomically promote independent database instances or roll promotion back.

## Checkpoint 4a: independent Todo and Notes bootstrap

The registry now activates both databases for replication, after the Todo-only
checkpoints above. Todo retains host TCP5432; Notes uses TCP5433, with internal
PostgreSQL TCP5432 in both pods. Initial preflight checks every primary volume
and refuses any existing standby data before the first base backup. Credential
transfer and streaming/slot verification read names from the Python registry.

A fresh pair of disposable overlays passed the real bootstrap playbook with
SELinux enforcing, fapolicyd and firewalld active. Both unique database markers
were readable on their corresponding read-only standbys. Each primary had its
own active, usable physical slot and streaming connection. The expanded suite
passed 161 tests; lint and both supported Ansible syntax checks passed.

Two preflight integration failures were repaired before bootstrap: fapolicyd
correctly refused reading untrusted package Python, and concurrent target
preflights raced while updating exact controller trust. Preflight now uses the
existing trust bridge and serializes hosts; it stages trusted code but does not
alter runtime or database state. Staging is reused only within one playbook run
for the same immutable package and target paths. No security service was disabled.

Private evidence: `/tmp/notes-dr-vms/two-app-bootstrap-serialized.log`,
`two-app-replication-evidence.log`, `bootstrap-backup-port-tests.log`; original
failed preflight logs remain alongside these. This is an incremental repaired
checkpoint, not the final unchanged-revision promotion/backup/rebuild acceptance.

## Checkpoint 4b: group promotion and both applications

On the same disposable pair, promotion was first refused while the primary was
reachable. Notes WAL replay was then paused and an extra Notes marker was
committed; observed local apply lag was 496 bytes. After the disposable primary
QEMU process exited, an actual `promote` attempt refused the Notes lag before
promoting either database. Both remained read-only and no decision record existed.
After replay resumed and both databases caught up, the guarded tool promoted both
and persisted a completed record listing Todo and Notes.

Application deployment before completed promotion was also refused. After the
successful group promotion, both application workloads, singular Keycloak and
shared proxy deployed through the existing workload bridge. All six exact
SourcePaths and original per-app database markers were checked. Real Chromium
passed bidirectional SSO, CRUD and cross-audience rejection with strict TLS;
OpenSSL verified both names against the same SAN certificate. The browser test
runtime received exact file trust in the disposable guest's separate test trust
file; SELinux and fapolicyd remained enforcing/active. Repeated application
recovery reported `changed=0`.

Evidence: `two-app-lag-refusal.log`, `two-app-primary-fenced.log`,
`two-app-promotion-live.log`, `two-app-refuse-premature-application.log`,
`two-app-promoted-deploy.log`, `two-app-promoted-repeat.log`, and
`two-app-promoted-browser-fixed.log` under `/tmp/notes-dr-vms`.
The browser harness initially used a virtualenv lacking Jinja2; using system
Python plus the already-installed browser test dependencies fixed the harness.
The complete suite passed 161 tests after replacing the old Ansible secret-read
source assertion with an executed Python secret-read/output-suppression test.
This remains a repaired development checkpoint, not final full DR acceptance.

## Checkpoint 4c: independent archives, backups and PITR

`postgres_backup` now loops over registry metadata, using the shared replication
bridge to refresh each database's inherited HBA entry against the local rootless
subnet. The same existing archive/PVC/SourcePath/security gates apply separately
to both databases. The backup CLI defaults status/create/mark to the full group;
disposable restore operations require an explicit app selection.

Live archive configuration passed on the promoted disposable host; its repeat
reported `changed=0`. Separate verified base backups were created in
`todo-postgres-backup` and `notes-postgres-backup`. For each database, a row was
inserted before a named restore point, then deleted and replaced by an after-point
row in the live database. Independent restores paused read-only at the named
point: before=1/after=0 in each restore versus before=0/after=1 live. Both restore
containers had `network=none` and no published ports. Exact-name cleanup removed
only their disposable resources; live after-point rows and both backup volumes
remained intact. Both archived WAL streams had no failures during this checkpoint.

Evidence: `two-app-backup-configure.log`, `two-app-backup-repeat.log`,
`two-app-base-backups.log`, `two-app-pitr-live.log` and `backup-role-final-tests.log`
under `/tmp/notes-dr-vms`. The full suite passed 162 tests, including actual shell
execution proving an inherited HBA subnet is replaced only for the selected
replication role and actual Ansible checks rejecting the other app's backup PVC.
Full fenced rebuild and unchanged-revision final acceptance remain pending.

## Checkpoint 4d: complete-group quarantine through Guest Agent

The quarantine helper now imports the service list from the trusted App registry.
It retains exact host/user validation, loaded-unit checks, inactive-or-failed
states, zero MainPID/ControlPID, no running user containers and preserved failure
evidence. The install playbook stages the same exact-file-trusted registry before
installing the helper. Unit tests include a failure isolated to Notes PostgreSQL.

On the disposable promoted Fedora guest, the existing SELinux opt-in tasks
installed the exact helper entrypoint label and persistent Guest Agent transition;
fapolicyd remained active. Fedora already enabled guest-exec RPCs by default;
this is distinct from the Oracle Linux allow-list opt-in covered by the policy
tests. Both QEMU NIC links were disconnected before invoking `check` and `stop`
through the real Guest Agent channel. Check observed the complete running stack;
stop succeeded with all six services stopped, zero service processes and no
running user containers. Both checkpoint VMs were then powered off, preserving
their data/backup disks. Neither protected acceptance VM was accessed.

Evidence: `quarantine-before-disconnect.log`, `quarantine-guest-agent-live.log`,
`quarantine-group-install-live.log`, `quarantine-group-tests.log` under
`/tmp/notes-dr-vms`. The full suite passed 163 tests; Ruff, ShellCheck, Ansible
lint and both Ansible syntax checks passed. Fenced two-app reseed and the final
unchanged-revision acceptance remain to be verified.
