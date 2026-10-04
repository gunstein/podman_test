# Oracle Linux acceptance with app-ops — 2026-10-04 (run 38)

**Clean pass:** `ae4ade9ba6d9b4a4093f2fc675e5cf9d29676fd1`.
Run `2026-10-04-app-ops-38`, a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, every step through `acceptance.py step`,
with no step added or changed. CI was green on the revision. It accepts G2
(`ae4ade9`): the DR check also says whether a host could take over.

- `install-dr-tool` (`05-1` `true`, `05-2` `false`) and `rebuild-standby`
  (`09-10`) wrote the operations package's revision and the host's offline
  bundle into the DR settings.
- Every `check monitor ... ok` passed with the new requirement, on both hosts
  and in all three places: after `install-dr-tool` (`05-7a`, `05-7b`), after
  the rebuild (`09-11i`, `09-11j`) and after both final reboots (`10-7d`,
  `10-7e`). Each journal ends with `Ready to take over: offline bundle
  ae4ade9ba6d9 with 7 image archives, all 13 DR secrets`: the bundle is the
  tested revision, all seven image archives are there, and each host holds
  every DR secret, the replication CA included. The operator's reviewer
  confirmed the line in `05-7a` and `10-7e`.
- After the fence the check still failed on the promoted host as required,
  naming "no standby streams" (`06-15`).

`acceptance.py report full` built `REPORT.md`: 115 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 61 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
The agent's chat copy of `REPORT.md` showed `09-11b` as `check connect
192.168.0.108 192.168.0.108 5433 open`; the file itself and the step's log
read `192.168.0.102 192.168.0.108`, as the guide says, so the copy was
mistyped, not the run. Apart from those, this record rests on `REPORT.md` and
the agent's final report.

The phases and their evidence otherwise match run 35's
([record](ACCEPTANCE-b9a9180.md)) step for step: nightly backup and restore on
`.102` (todo `base-20261004T121631Z`, notes `…121632Z`, keycloak
`…121633Z`, restore `{"changed": true}`, markers kept), bootstrap streaming
over TLS 1.3, quarantine rehearsal, fence, `failover`
(`{"changed": true, "promoted_now": true}`), application recovery,
`configure-backup` `false` twice, PITR (rows 42 and 43), one nightly backup on
`.108` (todo `base-20261004T131611Z`, notes `…131613Z`, keycloak
`…131615Z`), rebuild and both final reboots; backups 235/235/247 MiB, WAL
64 MiB each, 15046 MiB free (`10-8`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `6beb70cd-3a64-42aa-abcc-22dae6f297f0`, CA
`05:2A:5C:40:BC:6F:1B:84:CC:69:53:BB:A7:BE:48:00:79:54:89:A6:F0:16:DA:D7:77:98:D7:DD:5B:39:A1:4D`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag and its DR check timer, boot
`0f86048b-1053-4bd4-9429-e1a1a759bdab`. Markers 3, 4, 5, 41 and 44 and the
PITR rows 42 and 43 in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: the first C9.13 sudo refusal
check skipped (`04-4`); the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file.

Observations, as in runs 35 to 37: Podman's event lines in the timer
services' journals, SSH's post-quantum warning, and the quarantine stop's
warning that `todo-postgres.service` remained failed after a start with every
link down (`05-8-6`); the services were stopped as required (`05-8-7c`).
