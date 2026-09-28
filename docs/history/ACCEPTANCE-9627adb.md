# Oracle Linux acceptance with app-ops — 2026-09-28 (run 22)

**Clean pass:** `9627adb1f1e848ecb5c76fba818fb1e411e05633`.
Run `2026-09-28-app-ops-22`, executed by an agent under
`docs/ACCEPTANCE-AGENT.md` Part C, the command list run through
`deploy/scripts/lab/acceptance.py`. It accepts S1, the installer and DR
apart in the tree: the offline bundle carries only `app_installer`, the
operations package adds `deploy/dr`, and app-ops staged both
`app_installer` and `app_dr_host` on each host. Bootstrap, `failover`,
backup and PITR, and the standby rebuild all ran through the moved code.
It is also the first clean run with the fixes from runs 20 and 21: the
readiness check logged on a fixed line, every product log stamped with its
start, and `failover` run once in the background.

`acceptance.py report full` built `REPORT.md`: 104 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". The operator ran only the two
client trust scripts. The operator's reviewer checked the 57 product logs'
exit status and JSON in `REPORT.md`, and read `00-readiness`, `06-10`,
`08-9`, `08-10` and `11-4`, whose contents the tool does not judge.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Readiness `READY`, `exit=0`; both VM firewalls off; both VMs rolled back to `clean-agent` (links up, `onboot` 0); clean-host PASS, distinct machine IDs; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install `{"changed": true}`; HTTPS rule; services READY; CA `AE:C1:34:21:…`; headers; Chromium Todo, Notes and SSO passed, none skipped; markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for todo, notes and keycloak; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | DR and quarantine tools `true` then `false`; helper READY; profile; baseline open; firewall on; client SSH open, client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; IPv6 check; shutdown, links down, isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5 on `.108`; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed from the client and from `.108`; `app_dr.py preflight`; app-ops trusted, `recovery.yaml`, HTTPS rule; `failover` once, in the background (start 19:41:03): promote, deploy, backup, services, login-page and users each done, `{"changed": true, "promoted_now": true}`, address `192.168.0.108`, CA `CF:96:82:C2:…`, `next` says a browser confirms the login; status; `f\|off`; the guide's rolled-back write probes; markers |
| Application recovery | Services, CA `CF:96:82:C2:…` (the one `failover` printed), headers, browser tests; markers ID 41; `deploy-promoted-application` repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` (done by `failover`), later `false` again; `f\|off\|on\|1h` for all three; base backups; before and after rows inserted; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving |
| Rebuild | C9.10 in order: firewall on, isolated start, STOPPED, links up, SSH from `.108`, `.102`→`.108:22` blocked, services stopped; replication rules switched; SSH pinned; replication exception; wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 (only PostgreSQL) then VM 108, one at a time: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; backups 221/221/227 MiB, WAL 64 MiB each, 15096 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `14d7a597-9cd1-45f3-9467-c5c0459e0926`; VM 107 (`.102`,
todo-primary) database-only standby streaming over TLS, boot
`521e954e-a622-4aad-ac79-491960c4f69c`. Markers 3, 4, 5, 41, 42, 43 and 44
in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` and
`python3-pyyaml` installed on both VMs (documented prerequisites); the first
C9.13 sudo refusal check skipped; the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file.

Observations: in `06-10-failover.log` the JSON result comes before the last
progress line, because stdout and stderr travel over SSH separately. In
`10-3-cluster-status.log`, right after the standby's reboot, `receive_lsn`
for todo and notes is lower than `replay_lsn`: after a restart PostgreSQL
reports the received position from the start of the WAL segment. Lag was 0,
and `10-6` shows them equal again.
