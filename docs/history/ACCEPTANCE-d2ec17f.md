# Oracle Linux acceptance with app-ops — 2026-10-04 (run 37)

**Clean pass:** `d2ec17f4c4e058687e7cb51fdc5234eeedf443c8`.
Run `2026-10-04-app-ops-37`, a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, every step through `acceptance.py step`.
CI was green on the revision. It accepts D9 (`d2ec17f`): the VMs need no
Jinja2.

- `01-7-prerequisites-102` and `01-8-prerequisites-108` installed only
  `python3-pyyaml`, which the DR tools need; `python3-jinja2` was not
  installed on either VM, and the readiness check no longer required it.
- Everything that runs on the VMs passed without it: the single-host install
  and its second run (`03-2` `true`, `03-13` `false`), the nightly backup and
  restore (`03-12a` to `03-12f`), bootstrap, `install-dr-tool`, failover,
  `configure-backup`, PITR and rebuild.
- The deviation every earlier agent run recorded, "`python3-jinja2` still
  installed on both VMs (D9)", is gone from this one.

`acceptance.py report full` built `REPORT.md`: 115 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision (with the new `01-7` and `01-8`),
every step and log it names and nothing else; "Needs attention: Nothing". 61
product logs, each with the expected exit and JSON. The operator ran only the
two client trust scripts. This record is based on that `REPORT.md` and the
agent's final report; the individual logs were not read separately for it.

The phases and their evidence match run 35's
([record](ACCEPTANCE-b9a9180.md)) step for step: nightly backup and restore on
`.102` (todo `base-20261004T081300Z`, notes `…081301Z`, keycloak
`…081302Z`, restore `{"changed": true}`, markers kept), bootstrap streaming
over TLS 1.3, the DR check passing on both hosts and failing after the fence
as required (`06-15`), quarantine rehearsal, fence, `failover`
(`{"changed": true, "promoted_now": true}`), application recovery,
`configure-backup` `false` twice, PITR (rows 42 and 43), one nightly backup on
`.108` (todo `base-20261004T083625Z`, notes `…083627Z`, keycloak
`…083629Z`), rebuild, the DR check passing again, and both final reboots;
backups 235/235/247 MiB, WAL 64 MiB each, 15046 MiB free (`10-8`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `ae5a89a2-2121-453d-a126-1ae53ccb3b5e`, CA
`4A:99:89:B4:C5:BA:FC:25:C4:49:ED:FB:22:4E:06:B0:A0:E6:BB:A2:F8:19:84:98:B5:8F:CC:60:6E:11:7E:0F`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag and its DR check timer, boot
`097c6732-3a07-4a12-8b6f-9e2a754c8c31`. Markers 3, 4, 5, 41 and 44 and the
PITR rows 42 and 43 in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: the first C9.13 sudo refusal
check skipped (`04-4`); the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file, removed at the end.

Observations, as in runs 35 and 36: Podman's event lines in the timer
services' journals, SSH's post-quantum warning, and the quarantine stop's
warning that `todo-postgres.service` remained failed after a start with every
link down (`05-8-6`); the services were stopped as required (`05-8-7c`).
