# Manual recipes

These recipes help a technically interested person understand and test Todo by
hand. Follow them in order on disposable lab VMs:

1. [Prepare a VM](01-PREPARE-VM.md): rootless runtime and host prerequisites.
2. [Offline installation](02-OFFLINE-INSTALL.md): app, HTTPS and real login.
3. [Two-VM replication and DR](03-DR-TWO-VM.md): observe replication, then an
   explicitly approved disaster exercise.
4. [Backup and isolated PITR](04-BACKUP-PITR.md): compare restored and live data.

## How these differ from the reviewed procedures

These are learning and functional tests for a human operator. They cannot award
release acceptance. [Acceptance](../ACCEPTANCE.md) is the single canonical full
release procedure with phase evidence and unchanged-revision requirements.

Observations (`status`, `is-active`, read-only queries) are safe starting points.
Functional tests create test data or configure a dedicated lab. Fencing,
promotion, reseeding, reboot and restore cleanup require deliberate operator
approval and the linked safety procedures. Shorter explanations do not waive
any gate. Never reset, uninstall, reseed or rerun initial installation on a
working replicated or production-like pair to follow a recipe.

Addresses, VM IDs and user `todo` are examples. Verify identities before changing
anything. Commands name their execution location; use the service user for
rootless Podman, never sudo Podman. If an expected result is missing, stop at
that step, inspect service status/journal and read
[troubleshooting](../ACCEPTANCE-TROUBLESHOOTING.md). Never blindly retry a partial
destructive operation. Specialized fencing/quarantine/rebuild procedures are
linked rather than maintained as a second release runbook.
