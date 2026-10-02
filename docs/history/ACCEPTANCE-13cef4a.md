# Oracle Linux acceptance with app-ops — 2026-10-02 (run 31)

**Clean pass:** `13cef4a23c5ab90b3d302a955d3e28910363fd58`.
Run `2026-10-02-app-ops-31`, a new agent session under `docs/ACCEPTANCE-AGENT.md`
Part C, every step through `acceptance.py step`, with the guide unchanged
since run 30. It accepts D6 (target values on primary and standby) with real
Podman and systemd:

- The operations package carries the same `bundle.json` and
  `generated/target` as the offline bundle (`02-2`, verified in `02-5` and
  `02-6`), and the DR tools install those files, filled in by `target_render`
  on each host, instead of rendering units there.
- The primary records its public hostnames at install (`03-2`
  `{"changed": true}`, `03-13` `{"changed": false}`); publishing its databases
  for replication installed the bundle's replicated database units on
  `192.168.0.102` (bootstrap `true`, TLS streaming for all three).
- app-ops read the primary's hostnames and gave them to the standby with its
  own address (`04-10`); after the fence, `failover` deployed the promoted
  host with the names it had recorded and checked services, the login page
  and the users report with them (`todo.test`, `notes.test` at
  `192.168.0.108`); the deploy repeat was `false`.
- `rebuild-standby` gave the current primary's hostnames to the rebuilt
  standby with its own address (`09-10` `true`), which streams over TLS 1.3.
- `wait-ready.sh` takes the hostnames to check (default `todo.test
  notes.test`); every `check services` passed with it.

With the default values every file and check is what it was in run 30, so
this run shows D6 changes nothing for them; a hostname other than the
default is covered by unit tests only (backlog).

`acceptance.py report full` built `REPORT.md`: 104 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 58 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
The operator's reviewer read `00-readiness`, the ends of `03-2` and `03-13`,
the first two lines of `06-6` and `06-10`, the ends of `06-10`, `08-9`,
`08-10` and `11-4`, and the list of logs.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Readiness `READY for the agent run`, build `python3` `/usr/bin/python3` with Jinja2 and PyYAML, `exit=0`; both VM firewalls off; both VMs rolled back to `clean-agent` (links up, `onboot` 0); clean-host PASS; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install from the target files `{"changed": true}`; HTTPS rule; services READY; CA `C1:66:20:E0:…`; headers; Chromium Todo, Notes and SSO passed; test user provisioned; markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for todo, notes and keycloak; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | DR and quarantine tools `true` then `false`; profile; baseline open; firewall on; client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; IPv6 check; shutdown, links down, isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed from the client and from `.108`; `app_dr.py preflight` with its confirmation; `failover` once (start 20:02:15) with both confirmations: promote, deploy, backup, services, login-page and users each done, `{"changed": true, "promoted_now": true}`, hostnames `todo.test` and `notes.test` at `192.168.0.108`, CA `FD:BB:32:4E:…`; `f\|off`; write probes; markers |
| Application recovery | Services, CA `FD:BB:32:4E:…` (the one `failover` printed), headers, browser tests; markers ID 41; deploy repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` twice; `f\|off\|on\|1h` for all three; base backups; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving |
| Rebuild | C9.10 in order; wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag, archive healthy with 0 failures; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 (only PostgreSQL) then VM 108: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; backups 221/221/227 MiB, WAL 64 MiB each, 15096 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `8ac0348e-02e4-4846-9a7d-12d0b1d1bb5f`, CA `FD:BB:32:4E:…`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag, boot `19cbfa06-090d-40dd-a327-3522de1e2f57`. Markers
3, 4, 5, 41 and 44 and the PITR rows 42 and 43 in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: the guide still installs
`python3-jinja2` on both VMs, which the DR tools no longer need (backlog);
the first C9.13 sudo refusal check skipped (`04-4`); the operator ran the
client trust scripts (`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox
API token instead of the node Shell, and the testuser password in a tmpfs
file.

Observations, as in run 30: `06-10-failover.log` has the JSON before the last
progress line, and SSH to the Oracle Linux 9 VMs warns that the connection
does not use a post-quantum key exchange. Neither changes a result.
