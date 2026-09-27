# Oracle Linux acceptance with app-ops — 2026-09-27 (run 19)

**Functional pass, not clean:** `4137c5ebbe1c536d4d7edecf065b7cfc1a08f15d`.
Run `2026-09-27-app-ops-19`, executed by an agent under
`docs/ACCEPTANCE-AGENT.md` Part C, the command list run through
`deploy/scripts/acceptance.py`. It is the first full run with the real time
limits (G6), the report that compares every record under a label, and
`failover` with its honest `services` and `login-page` steps. Every
functional gate passed; none of the new limits was reached.

Not clean: step 07-5 `check services 192.168.0.108 app` failed once and
passed when repeated 43 seconds later. `wait-ready.sh` printed READY and
nginx was valid both times; the only failure was a transient systemd unit
`…-29e5e1da076debce.service` running `/usr/bin/podman healthcheck run` for
one container. Podman runs each health check as such a unit; one run had
failed while the container started (the liveness probe allows three), and
the unit showed `failed` until the next run passed. No service restarted
(`NRestarts` 0). The check counted Podman's own health-check units as failed
services; it now ignores them and lists them instead, since `wait-ready.sh`
already requires every container to be healthy. No product defect. The agent
called this a REPAIRED FUNCTIONAL PASS, but nothing was repaired: a
read-only check was repeated, as in run 14.

`acceptance.py report full` built `REPORT.md`: 105 records, 104 PASS and the
one FAIL above, none refused or unfinished; the checkout clean at the same
revision at every step; compared with the guide at that revision, every step
and every log it names and nothing else. The operator's reviewer checked the
57 product logs' exit status and JSON, and read both `07-5` logs, `06-10`,
`08-9`, `08-10` and `11-4`.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Both VM firewalls off; both VMs rolled back to `clean-agent` (links up, `onboot` 0); clean-host PASS, distinct machine IDs; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install `{"changed": true}`; HTTPS rule; services READY; CA `C4:12:FE:1A:…`; headers; Chromium Todo, Notes and SSO passed, none skipped; markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for todo, notes and keycloak; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | DR and quarantine tools `true` then `false`; helper READY; profile; baseline open; firewall on; client SSH open, client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; IPv6 check; shutdown, links down, isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5 on `.108`; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed from the client and from `.108`; `app_dr.py preflight`; app-ops trusted, `recovery.yaml`, HTTPS rule; `failover` once: promote, deploy, backup, services, login-page and users each done, `{"changed": true, "promoted_now": true}`, address `192.168.0.108`, CA `89:53:59:84:…`, `next` says a browser confirms the login; status; `f\|off`; the guide's rolled-back write probes; markers |
| Application recovery | Services (07-5 failed once, passed on a repeat: see above), CA `89:53:59:84:…` (the one `failover` printed), headers, browser tests; markers ID 41; `deploy-promoted-application` repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` (done by `failover`), later `false` again; `f\|off\|on\|1h` for all three; base backups; before and after rows inserted; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving |
| Rebuild | C9.10 in order: firewall on, isolated start, STOPPED, links up, SSH from `.108`, `.102`→`.108:22` blocked, services stopped; replication rules switched; SSH pinned; replication exception; wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 (only PostgreSQL) then VM 108, one at a time: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; backups 221/221/227 MiB, WAL 64 MiB each, 15096 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `cddd350c-223d-4c05-b9ad-51fef7e3c53f`; VM 107 (`.102`,
todo-primary) database-only standby streaming over TLS, boot
`cffbaf2b-0490-452b-b8df-813a2ae2d5d3`. Markers 3, 4, 5, 41, 42, 43 and 44
in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` and
`python3-pyyaml` installed on both VMs (documented prerequisites); the first
C9.13 sudo refusal check skipped; the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file.
