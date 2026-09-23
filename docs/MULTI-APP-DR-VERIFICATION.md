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
