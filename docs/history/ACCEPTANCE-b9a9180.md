# Oracle Linux acceptance with app-ops — 2026-10-03 (runs 34 and 35)

**Clean pass:** `b9a9180a053a337df0f7a09820d1c051f1b84e34` (run 35).
Run `2026-10-03-app-ops-35`, a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, every step through `acceptance.py step`.
It accepts, with real Podman, systemd and PostgreSQL:

- **B1, backups on a single host** (`3488a24`). `install.sh` on `.102` had
  turned on `todo-backup.timer`; one run of `todo-backup.service` took a
  verified base backup of every database (todo `base-20261003T182545Z`,
  notes `…182546Z`, keycloak `…182547Z`, `03-12a`). A Todo row written after
  it (`03-12b`) was gone after `app_installer backup restore` restored all
  three databases (`03-12c`: `todo: restored base-20261003T182545Z` and the
  same for notes and keycloak, `{"changed": true}`; `03-12d`: `row_gone`
  `1`), the services came
  back (`03-12e`) and the markers written before the backup were still there
  (`03-12f`). The second install stayed `{"changed": false}` (`03-13`), so
  the timer is idempotent too.
- **M4, a durable WAL archive** (`de0a671`). `failover` configured archiving
  with the new archive command (copy to a temporary name, sync, rename, sync
  the directory); `configure-backup` was `false` twice afterwards (`08-1`,
  `08-12`), `f|off|on|1h` before and after a reboot (`08-2`, `08-14`,
  `10-5a`), both Todo and Notes PITR restores paused at their restore point
  with only row 42, while the live tables held 42 and 43 (`08-9`, `08-10`),
  `app_backup.py status` showed 0 failed archive attempts (`08-15`), and
  `cluster-status` reported the archive healthy with 0 historical
  failures for all three databases (`09-11d`, `10-3`, `10-6`).
- **Q5 and Q6** (`5e3ef8a`): the code pyright and ruff's bugbear rules made
  change (timeouts, `keycloak.request`, the inventory role check, the
  rewritten loops) ran in every phase without a failure.
- **The readiness guard** (`b9a9180`). `logs/00-readiness.log` ends with
  `exit=0`, and the first step ran only after it, as the guard requires.

`acceptance.py report full` built `REPORT.md`: 115 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 61 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
The operator's reviewer read `00-readiness` (build `python3`
`/usr/bin/python3` with Jinja2 and PyYAML, `READY for the agent run.`,
`exit=0`), the ends of `03-2`, `03-13`, `05-1`, `05-2`, `03-12a` to `03-12e`,
`08-4`, `08-16`, `06-15`, `06-10`, `08-9`, `08-10`, `08-15` and `11-4`.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Readiness `exit=0`; both VM firewalls off; both VMs rolled back to `clean-agent`; clean-host PASS; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install `{"changed": true}`; HTTPS rule; services READY; CA `E6:07:F0:F7:…`; headers; Chromium Todo, Notes and SSO passed; markers ID 3; reboot, same CA, markers; nightly backup, row, restore `true`, services, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for all three; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| DR tool, check, quarantine | `install-dr-tool` `true` then `false`; DR check passed on both hosts; quarantine tools `true` then `false`; profile; baseline open; firewall on; blocked paths timed out; IPv6 check; isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed; `app_dr.py preflight`; `failover` once, `{"changed": true, "promoted_now": true}`, `todo.test` and `notes.test` at `192.168.0.108`, CA `CB:FD:AE:84:…`; `f\|off`; write probes; markers; DR check failed with "no standby streams", as required |
| Application recovery | Services (failed DR check noted, not counted), CA `CB:FD:AE:84:…`, headers, browser tests; markers ID 41; deploy repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` twice; `f\|off\|on\|1h`; base backups; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving; one nightly backup run: three verified backups (`base-20261003T185241Z`, `…185243Z`, `…185244Z`), pruned |
| Rebuild | Wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag, archive healthy; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; DR check passed on both hosts; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 then VM 108: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; DR check passed on both; backups 235/235/247 MiB, WAL 64 MiB each, 15046 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application,
WAL archive and both timers, boot `519a7e6a-c6a5-473b-b71f-366cfa8afb84`, CA
`CB:FD:AE:84:50:15:11:11:A6:72:0E:15:BB:D9:3B:2E:A6:E8:4F:21:B9:F8:31:CA:72:D5:FE:4E:18:51:9F:97`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag and its DR check timer, boot
`dfc01767-9bf9-47b5-88b4-2798cd331590`, Proxmox firewall off, `onboot` 0.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` still
installed on both VMs (backlog D9); the first C9.13 sudo refusal check skipped
(`04-4`, `CLIENT_SUDO: no`); the operator ran the client trust scripts; the
A3 lab sudoers file, a Proxmox API token instead of the node Shell, and the
testuser password in a tmpfs file, removed at the end.

Observations: as in run 32, the journal of the timer services also holds
Podman's own `container exec_died` event lines, and SSH warns about the
missing post-quantum key exchange; neither is an error. The isolated quarantine stop (`05-8-6`) warned that
`todo-postgres.service` remained failed after the VM had started with every
link down; the helper keeps a failed state for inspection, as it is written
to, and the services were stopped as required (`05-8-7c`).

## Run 34 (`b9a9180`'s parent, `06c91d7`)

Run `2026-10-03-app-ops-34` passed every phase but was **not clean**: the
agent ran the readiness check (C1a) in the terminal, not into
`logs/00-readiness.log`, so the run had no record of it, as in run 21. Only
the operator's summary was received, not its `REPORT.md` or logs. The
product was not at fault; `b9a9180` made the first step refuse until that log
shows a passing readiness check, and run 35 is the clean run of that guard.
