# Oracle Linux acceptance with app-ops — 2026-09-27 (run 16)

**Clean pass:** `7402641c04a26f0d69c415b249ca7caccf112009`.
Run `2026-09-27-app-ops-16`, executed by an agent under
`docs/ACCEPTANCE-AGENT.md` Part C, the command list run through
`deploy/scripts/acceptance.py`. It is the first clean pass that includes
replication over TLS (T1), Keycloak lockout and password policy (H1), the
nginx security headers with the Notes CSP fix (H2), and the acceptance tool
itself (A1-A4).

`acceptance.py report full` built `REPORT.md`: 104 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
and, compared with the guide at that revision, every step and every log it
names and nothing else. The operator ran only the two client trust scripts.
The operator's reviewer checked the 58 product logs' exit status and JSON in
`REPORT.md`, and read `08-9-restore-todo.log` and `08-10-restore-notes.log`,
whose contents the tool does not judge.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Both VM firewalls off; both VMs rolled back to `clean-agent` (links up, `onboot` 0); clean-host PASS, distinct machine IDs; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install `{"changed": true}`; HTTPS rule running and permanent; services READY, `nginx -t`; CA `B3:06:55:10:…`; headers (HSTS, `connect-src 'self' https://todo.test:8443`, `DENY`, referrer policy); Chromium Todo, Notes and SSO passed, none skipped; markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways, host keys verified; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; only `initial.yaml` new; `streaming`, `t`, `TLSv1.3` for todo, notes and keycloak; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | DR and quarantine tools `true` then `false`; helper READY; profile with three rules, firewall off; baseline open; firewall on (20 s); client SSH open, client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; no global IPv6; shutdown, links down, isolated start, STOPPED; links up, services stopped, no containers; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and promote | Markers ID 5 on `.108`; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; `ports-closed.sh` CLOSED from the client and from `.108`; preflight, promote, status; `f\|off`; the guide's rolled-back write probes; markers |
| Application recovery | app-ops trusted, `recovery.yaml`, HTTPS rule; deploy `true`, later `false`; services, CA `66:AF:E3:D5:…`, headers, browser tests; markers ID 41; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `true`, later `false`; `f\|off\|on\|1h` for all three; three verified base backups; no restore state before; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup left no restore resources; reboot, archiving |
| Rebuild | C9.10 in order: firewall on, isolated start, STOPPED, links up, SSH from `.108`, `.102`→`.108:22` blocked, services stopped; replication rules switched; SSH pinned; replication exception (20 s); wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 (only PostgreSQL) then VM 108, one at a time: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; backups 189/189/211 MiB, WAL 80 MiB each, 15176 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `668e6ec8-da4e-491c-81d4-0929b34b567a`; VM 107 (`.102`,
todo-primary) database-only standby streaming over TLS, boot
`913dc2d4-a79f-450b-8d89-0f45e3568eee`. Markers 3, 4, 5, 41, 42, 43 and 44
in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` installed on
both VMs (documented prerequisite); the first C9.13 sudo refusal check
skipped; the operator ran the client trust scripts (`CLIENT_SUDO: no`); the
A3 lab sudoers file, a Proxmox API token instead of the node Shell, and the
testuser password in a tmpfs file.
