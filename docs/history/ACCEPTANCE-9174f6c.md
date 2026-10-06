# Oracle Linux acceptance with app-ops — 2026-10-06 (run 45)

**Clean pass:** `9174f6c9ee22910435583e5c7222b0f651576055`.
Run `2026-10-06-app-ops-45`, a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, every step through `acceptance.py step`,
with no step added or changed and no step call refused or repeated. CI was
green on the revision. It accepts G3 (`9174f6c`): the report times the
failover against the 30-minute goal.

- `REPORT.md`: "Failover (G3): 5 min 18 s from the fence of the old primary
  (06-3) to users logging in to both apps on the promoted host (07-8); the
  goal is under 30 min 00 s." That includes `failover` (1 min 59 s), the
  checks after it and the operator's client trust step for `.108`.
- The whole run took 42 min 46 s first start to last end, 33 min 22 s in
  steps and 9 min 54 s between them (run 44: 45 min 38 s, 13 min 02 s).

`acceptance.py report full` built `REPORT.md`: 121 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 65 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
This record rests on `EVIDENCE.md`; the agent wrote no final report file
this time, only the run record line and its chat summary.

The rest matches run 44's ([record](ACCEPTANCE-7ab33fc.md)) step for step:
nightly backup and restore on `.102` (todo `base-20261006T171309Z`, notes
`…171311Z`, keycloak `…171312Z`, `row_gone` 1); bootstrap streaming over TLS
1.3; every `check monitor ... ok` ready to take over with bundle
`9174f6c9ee22`; `failover` `{"changed": true, "promoted_now": true}`; `06-15`
failing as required; Todo restored to `acceptance_before_after` and Notes to
`2026-10-06T17:32:25Z`, each holding only row 42; 0 failed archive attempts;
`rebuild-standby` `true`; the second reseed refusing the wrong name and then
`{"changed": true}` with the same `*_rebuilt_standby` slots streaming at 0
lag and the standby's positions moved from `0/10…`/`0/11…` to `0/12…`/`0/13…`;
the `phase9b` marker (ID 45) on `.102`; backups 267/283/279 MiB, 14850 MiB
free; `NRestarts` 0, 0, 0.

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `8f41505e-8dbb-4388-93eb-5acc16d5c497`, CA
`9C:61:DF:46:0B:37:C7:FB:A8:63:26:0B:50:BA:8C:6B:89:DC:37:ED:22:5D:36:BA:46:8D:4E:40:75:FB:82:3D`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag, boot `3f35e7e2-57ee-449a-8a5b-472dbef3fc50`, Proxmox
firewall off, `onboot` 0.

## Deviations

Expected (C7), which do not change the verdict: the first C9.13 sudo refusal
check skipped (`04-4`); the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file, removed at the end.
