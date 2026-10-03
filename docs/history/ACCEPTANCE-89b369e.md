# Oracle Linux acceptance with app-ops — 2026-10-03 (run 32)

**Clean pass:** `89b369ea02f5a63709a1a124fe82b4fb0e3e58b6`.
Run `2026-10-03-app-ops-32`, a new agent session under `docs/ACCEPTANCE-AGENT.md`
Part C, every step through `acceptance.py step`. It accepts M1 and M2
(`984c0b6`) with real Podman, systemd and PostgreSQL, and the CI fix after it
(`89b369e`):

- **M1, the scheduled DR check.** `install-dr-tool` installed `app_dr.py`, its
  settings and `todo-dr-check.timer` on both hosts (`05-1` `true`, `05-2`
  `false`). One run of `todo-dr-check.service` passed on each host with its
  report in the journal: `.102` primary, 1 standby streaming over TLS per
  database, WAL archiving off, disk 85 % free (`05-7a`); `.108` standby,
  receiving WAL, 89 % free (`05-7b`). After the fence and failover it failed
  on `.108`, as it must, naming "no standby streams from this primary over
  TLS" for todo, notes and keycloak (`06-15`); `check services` then passed
  and noted the failed check (`07-5`, `dr_check=failed (see check monitor)`).
  After `rebuild-standby` it passed on both hosts again, `.108` with a healthy
  WAL archive (`09-11i`, `09-11j`), and once more after both final reboots, so
  both timers came back (`10-7d`, `10-7e`).
- **M2, the nightly backup.** `failover` installed `todo-backup.timer` with
  `configure-backup` (`08-1` and `08-12` `false`). One run of
  `todo-backup.service` (`08-16`) took and verified a base backup of every
  database (todo `base-20261003T043457Z`, notes `…043500Z`, keycloak
  `…043502Z`) and pruned: 0 backups older than 7 days, and
  `pg_archivecleanup` removed the archived WAL older than the oldest kept
  backup (the unit passed, and its script runs with `sh -e`). Backups
  235/235/247 MiB, WAL 64 MiB each, 15046 MiB free afterwards (`10-8`).
- **CI.** CI was red from `13cef4a` (run 31) to `984c0b6`: the full-stack
  job's "failover's login-page check" still called `failover.login_page()`
  without the hostnames D6 added, a `TypeError` in a Python snippet inside the
  workflow YAML. Product code was not affected; run 31's kickoff nevertheless
  said "CI green" for a red revision. `89b369e` fixed the call; CI is green on
  it, and this run's kickoff was given only after that. Backlog Q5 moves such
  snippets into `.py` files a checker reads.

`acceptance.py report full` built `REPORT.md`: 112 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 58 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
The operator's reviewer read `00-readiness`, the ends of `03-2`, `03-13`,
`05-1`, `05-2`, `05-7a`, `05-7b`, `06-15`, `08-16`, `09-11i`, `10-7e`,
`06-10`, `08-9`, `08-10` and `11-4`.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Readiness `READY for the agent run`, build `python3` `/usr/bin/python3` with Jinja2 and PyYAML; both VM firewalls off; both VMs rolled back to `clean-agent`; clean-host PASS; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install `{"changed": true}`; HTTPS rule; services READY; CA `F0:F9:87:69:…`; headers; Chromium Todo, Notes and SSO passed; markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for all three; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| DR tool, check, quarantine | `install-dr-tool` `true` then `false`; DR check passed on both hosts; quarantine tools `true` then `false`; profile; baseline open; firewall on; blocked paths timed out; IPv6 check; isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed; `app_dr.py preflight`; `failover` once, every step done, `{"changed": true, "promoted_now": true}`, `todo.test` and `notes.test` at `192.168.0.108`, CA `39:E3:CC:BA:…`; `f\|off`; write probes; markers; DR check failed with "no standby streams", as required |
| Application recovery | Services (failed DR check noted, not counted), CA `39:E3:CC:BA:…`, headers, browser tests; markers ID 41; deploy repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` twice; `f\|off\|on\|1h`; base backups; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving; one nightly backup run: three verified backups, pruned |
| Rebuild | Wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag, archive healthy; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; DR check passed on both hosts; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 then VM 108: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; DR check passed on both; backups 235/235/247 MiB, WAL 64 MiB each, 15046 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application,
backup and both timers, boot `726af5bd-236f-4954-9dc5-4a0302a014fc`, CA
`39:E3:CC:BA:…`; VM 107 (`.102`, todo-primary) database-only standby streaming
over TLS 1.3 with 0 bytes apply lag and its DR check timer, boot
`f2cf2b8f-f309-4e5e-8227-7a28794626af`. Markers 3, 4, 5, 41 and 44 and the PITR
rows 42 and 43 in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` still
installed on both VMs (backlog D9); the first C9.13 sudo refusal check skipped
(`04-4`); the operator ran the client trust scripts (`CLIENT_SUDO: no`); the
A3 lab sudoers file, a Proxmox API token instead of the node Shell, and the
testuser password in a tmpfs file.

Observations: the journal of `todo-dr-check.service` and
`todo-backup.service` also holds Podman's own event lines (`container
exec_died`, `container remove`), because Podman logs the events of the
commands a unit runs under that unit. They are noise, not errors, and the
checks read only the tools' own lines. As before, SSH to the VMs warns that
the connection does not use a post-quantum key exchange, and
`06-10-failover.log` has the JSON before the last progress line.
