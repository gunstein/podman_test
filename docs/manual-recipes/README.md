# Manual recipes

These are informal, condensed walkthroughs for setting up VMs and exercising
the Todo application, DR and backup features by hand. They are a separate,
simpler path alongside the reviewed procedures elsewhere in the repository,
not a replacement for them.

- [Prepare an Oracle Linux 9 VM](01-PREPARE-VM.md)
- [Offline install on one VM](02-OFFLINE-INSTALL.md)
- [Two-VM DR walkthrough](03-DR-TWO-VM.md)
- [Backup and PITR on one VM](04-BACKUP-PITR.md)

## How these differ from the reviewed procedures

- [`docs/ACCEPTANCE.md`](../ACCEPTANCE.md) is the canonical, reviewed
  acceptance sequence: explicit PASS/STOP criteria per phase, separate
  operator approvals before every destructive step, and evidence
  requirements. These recipes skip most of that ceremony to get to a working
  system faster, and are meant for a single operator experimenting on
  disposable lab VMs, not for validating a release.
- Fencing and quarantine here use the same `todo-quarantine.sh` tool and
  Guest Agent flow as the reviewed procedure (see
  [Proxmox quarantine](../PROXMOX-QUARANTINE.md)) rather than manual VM
  console access, because that tool exists specifically to avoid typing
  commands into an isolated guest's console.
- If a step fails partway through a destructive operation (rebuild, restore),
  stop and read [Acceptance troubleshooting](../ACCEPTANCE-TROUBLESHOOTING.md)
  instead of retrying blindly; these recipes do not restate that guidance
  inline.
- Addresses, VM IDs and the service username (`todo`) are examples; replace
  them with your own values throughout.
