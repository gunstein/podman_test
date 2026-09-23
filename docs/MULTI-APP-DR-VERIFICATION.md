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
