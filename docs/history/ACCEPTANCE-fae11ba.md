# Oracle Linux acceptance with app-ops — 2026-10-04 (runs 39 and 40)

**Clean pass:** `fae11ba54109869dc4fb1809e7c06708b994a8ae` (run 40).
Run `2026-10-04-app-ops-40`, a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, every step through `acceptance.py step`,
with no step added or changed. CI was green on the revision. It accepts M5
(`fae11ba`): a restore to a time.

- `08-7` wrote the restore point `acceptance_before_after` in all three
  databases and printed the time just after it, `2026-10-04T16:21:11Z`.
- `08-10` restored Notes with `--target-time 2026-10-04T16:21:11Z` and no
  `--backup`: it picked the newest base backup before that time by itself,
  `base-20261004T162058Z`, and reported `PITR from base-20261004T162058Z
  paused at 2026-10-04T16:21:11Z. Live database was not modified.` The
  restored copy (`recovery|paused|read_only = t|t|on`, network `none`) held
  only row 42, the live table 42 and 43.
- `08-9` restored Todo to the named point as before (`base-20261004T162057Z`,
  paused at `acceptance_before_after`, only row 42), so both kinds of target
  pass in the same run.
- `08-15` showed 0 failed archive attempts after both restores, and
  `cluster-status` reported the archive healthy with 0 historical failures
  for all three databases (`09-11d`, `10-3`, `10-6`).

G2 held again: every `check monitor ... ok` (`05-7a`, `05-7b`, `09-11i`,
`09-11j`, `10-7d`, `10-7e`) ended with `Ready to take over: offline bundle
fae11ba54109 with 7 image archives, all 13 DR secrets`, and after the fence
`06-15` failed as required, naming "no standby streams" for all three
databases.

`acceptance.py report full` built `REPORT.md`: 115 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 61 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
This record rests on `EVIDENCE.md`, built after the run with the then new
`acceptance.py evidence` (`b401627`): `REPORT.md`, the agent's
`run-record.md` and the end of each key log.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Readiness `exit=0`; both VM firewalls off; both VMs rolled back to `clean-agent`; clean-host PASS; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install `{"changed": true}`; HTTPS rule; services READY; CA `C1:49:2F:5F:…`; headers; Chromium Todo, Notes and SSO passed; markers ID 3; reboot, same CA, markers; nightly backup (todo `base-20261004T155850Z`, notes `…155851Z`, keycloak `…155852Z`), row, restore `true`, `row_gone` 1, services, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for all three; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| DR tool, check, quarantine | `install-dr-tool` `true` then `false`; DR check passed and ready on both hosts; quarantine tools `true` then `false`; profile; baseline open; firewall on; blocked paths timed out; IPv6 check; isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed; `app_dr.py preflight`; `failover` once, `{"changed": true, "promoted_now": true}`, `todo.test` and `notes.test` at `192.168.0.108`, CA `92:BB:B6:47:…`; `f\|off`; write probes; markers; DR check failed with "no standby streams", as required |
| Application recovery | Services (failed DR check noted, not counted), CA `92:BB:B6:47:…`, headers, browser tests; markers ID 41; deploy repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` twice; `f\|off\|on\|1h`; base backups; Todo restored to a named point and Notes to a time, each holding only row 42 while the live tables held 42 and 43; cleanup; reboot, archiving; one nightly backup run: three verified backups (`base-20261004T162318Z`, `…162320Z`, `…162321Z`), pruned |
| Rebuild | Wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag, archive healthy; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; DR check passed and ready on both hosts; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 then VM 108: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; DR check passed on both; backups 235/251/247 MiB, WAL 64/80/64 MiB, 15030 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `e51ebe19-5e61-4a25-b9f6-c888d528dbf8`, CA
`92:BB:B6:47:54:BE:D3:D9:59:29:64:14:B9:BA:9F:6C:99:75:58:D6:BB:D2:57:59:7B:7D:8A:91:F1:29:09:1A`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag and its DR check timer, boot
`5f946e9b-7ab0-4123-9857-107735173c47`, Proxmox firewall off, `onboot` 0.

## Deviations

Expected (C7), which do not change the verdict: the first C9.13 sudo refusal
check skipped (`04-4`); the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file, removed at the end.

Process, noted by the agent, none of which changes a result:

- `04-11` and `04-12` ran in one shell command, against the rule of one step
  per command. Both are read-only `status` calls and both logs hold
  `{"changed": false}`.
- Two step labels were mistyped (`03-3-firewall`, `05-7-quarantine-profile`);
  `acceptance.py` refused both with exit 3 and ran nothing.
- The agent's terminal dropped the first output line of some steps (`03-12c`,
  `08-7`, `04-3`, `06-8`, `11-4`); the logs hold them, and the agent checked
  each there.
- Readiness was first run in run 39's folder; no step ran there.

Observations, as before: Podman's event lines in the timer journals and
SSH's post-quantum warning (now left out of `EVIDENCE.md` and counted); the
isolated quarantine stop (`05-8-6`, `09-3`) keeps the failed PostgreSQL units
for inspection, as written to. The readiness log showed that warning as the
detail of the passing SSH check and the PyYAML install advice on a passing
PyYAML check; both show only on failure since `a359402`.

## Run 39 (`fae11ba`)

Run `2026-10-04-app-ops-39` has **no verdict**. Its agent session ran out of
context mid-run and left VM 107 fenced; the run was stopped there. The
product was not at fault. Run 40 started again from the clean snapshots with
a new run ID and a new agent session.
