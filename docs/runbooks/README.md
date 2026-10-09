# Runbooks

Short pages for real incidents, one page each, for an operator without the
developer at hand. Each says how you notice it, what to check first, what to
do, and what never to do. [ACCEPTANCE.md](../ACCEPTANCE.md) is the drill
these come from: it explains every step at length and is the place to look
when a page here is not enough.

In the examples Oslo is the primary `todo-primary` (`192.168.0.102`) and
Trondheim the standby `todo-standby` (`192.168.0.108`), service user
`gunstein`, as in the lab. Use your own names and addresses. Commands run as
the service user unless they say `sudo`.
Where each log is, and what to do when `journalctl --user` shows nothing:
[Logging](../LOGGING.md).

| What happened | Page | Tested in acceptance |
|---|---|---|
| Oslo is lost (fire, power, hardware): users cannot work | [primary-lost.md](primary-lost.md) | Yes, every run (phases 6 and 7) |
| A timer failed: `todo-dr-check`, `todo-backup` or `todo-replication-tls` | [timer-failed.md](timer-failed.md) | The failure and its message (phase 6); the fixes are the other pages |
| The standby is down, behind, or lost its slot | [standby-rebuild.md](standby-rebuild.md) | The rebuild after a failover and the re-seed without one (both phase 9) |
| The disk is filling up | [disk-full.md](disk-full.md) | No |
| A certificate expires or has expired | [certificates.md](certificates.md) | Partly (nginx renews at start; replication renewal is tested in the unit tests, not in a lab run) |
| Data was deleted or changed by mistake | [data-mistake.md](data-mistake.md) | Yes, to a named restore point and to a time (phase 8) |
| A single host (no DR) must go back to last night | [single-host-restore.md](single-host-restore.md) | Yes, every run (phase 3) |

## Before an incident

Check these when the pair is set up, and again after every change; a missing
piece found during a fire is found too late.

- The DR check passes on both hosts and ends with `Ready to take over: ...`:
  `journalctl --user -u todo-dr-check.service -n 20 -o cat`. A failed unit
  shows in `systemctl --user --failed`. Someone must look at it; nothing
  pages anyone.
- Both hosts have the offline bundle and the operations package of the same
  revision, extracted in the service user's home (`~/todo-offline-m12`,
  `~/todo-operations`). The DR check verifies the bundle.
- DNS: the records for the public names (`todo.test`, `notes.test`) have a
  short TTL (a few minutes), can be changed without Oslo, and you know who
  changes them and how to reach that person.
- Clients can be told to trust Trondheim's CA after a failover, or already
  do (backlog T4 would give both sites one CA).
- You know how to stop the Oslo machine for certain without Oslo's help: the
  hypervisor, power or network. That is the fencing step, and it decides
  everything else (backlog T3).
