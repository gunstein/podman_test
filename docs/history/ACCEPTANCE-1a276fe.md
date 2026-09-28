# Oracle Linux acceptance with app-ops — 2026-09-28 (run 21)

**Functional pass, not clean:** `1a276fee297e748bca799d5dc5ab8d7dd1d29a35`.
Run `2026-09-28-app-ops-21`, executed by an agent under
`docs/ACCEPTANCE-AGENT.md` Part C, the command list run through
`deploy/scripts/acceptance.py`. It is the first run with the fixes from run
20: every product log starts with its time, a product step refuses to run
twice, and `failover` runs in the background. `failover` ran once and
promoted (`"promoted_now": true`), and all 104 tool steps passed.

Not clean: `logs/00-readiness.log` was missing, and `report full` listed it
under "Needs attention". The agent says it ran the read-only readiness check
before phase 1 but not into that log, and did not write the log afterwards.
Without the log there is no record of the check, so the run is not clean.
The guide showed the readiness command in prose with "save it to" instead of
a fixed line; C1a now gives the exact line, which appends each attempt with
its time and exit status. No product defect.

`acceptance.py report full` built `REPORT.md`: 104 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names except
`00-readiness.log`, and nothing else. The operator's reviewer checked the 56
product logs' exit status and JSON, and read `06-10`, `08-9`, `08-10` and
`11-4`.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Both VM firewalls off; both VMs rolled back to `clean-agent` (links up, `onboot` 0); clean-host PASS, distinct machine IDs; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install `{"changed": true}`; HTTPS rule; services READY; CA `8C:25:B7:97:…`; headers; Chromium Todo, Notes and SSO passed, none skipped; markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for todo, notes and keycloak; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | DR and quarantine tools `true` then `false`; helper READY; profile; baseline open; firewall on; client SSH open, client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; IPv6 check; shutdown, links down, isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5 on `.108`; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed from the client and from `.108`; `app_dr.py preflight`; app-ops trusted, `recovery.yaml`, HTTPS rule; `failover` once, in the background (start 16:33:37): promote, deploy, backup, services, login-page and users each done, `{"changed": true, "promoted_now": true}`, address `192.168.0.108`, CA `8F:8F:9A:D6:…`, `next` says a browser confirms the login; status; `f\|off`; the guide's rolled-back write probes; markers |
| Application recovery | Services, CA `8F:8F:9A:D6:…` (the one `failover` printed), headers, browser tests; markers ID 41; `deploy-promoted-application` repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` (done by `failover`), later `false` again; `f\|off\|on\|1h` for all three; base backups; before and after rows inserted; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving |
| Rebuild | C9.10 in order: firewall on, isolated start, STOPPED, links up, SSH from `.108`, `.102`→`.108:22` blocked, services stopped; replication rules switched; SSH pinned; replication exception; wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 (only PostgreSQL) then VM 108, one at a time: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; backups 221/221/227 MiB, WAL 64 MiB each, 15096 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `38e4cccf-7722-4d28-ac7a-ddd1e398b384`; VM 107 (`.102`,
todo-primary) database-only standby streaming over TLS, boot
`46f1ddb0-c840-4c3f-99e4-633f547ae7dc`. Markers 3, 4, 5, 41, 42, 43 and 44
in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` and
`python3-pyyaml` installed on both VMs (documented prerequisites); the first
C9.13 sudo refusal check skipped; the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file.
