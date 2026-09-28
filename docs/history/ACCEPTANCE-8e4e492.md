# Oracle Linux acceptance with app-ops — 2026-09-28 (run 20)

**Functional pass, not clean:** `8e4e4929840bfcd7914c27e08d6969a11568a277`.
Run `2026-09-27-app-ops-20`, executed by an agent under
`docs/ACCEPTANCE-AGENT.md` Part C, the command list run through
`deploy/scripts/acceptance.py`. Every functional gate passed, and
`REPORT.md` said ALL STEPS PASS, but step 06-10 `failover` ran twice.

What happened, from the timestamps and the agent's account: `06-6`
preflight passed at 05:22:31, so no promotion existed then. A first
`failover` started after `06-9` (05:22:35) and promoted the whole group; the
promotion record was written `complete` at 05:22:43. At 05:22:52 the agent's
own harness reached its 600-second limit for the batch of commands it had
started, and killed the SSH session, while `failover` was deploying. The
agent checked `app_dr.py status` and ran `failover` again at 05:25. That run
found the complete record, skipped the promotion and finished the other
steps (`"promoted_now": false`). The guide's `product` helper wrote each log
with `>`, so the second run replaced the first run's log and the report
could not see the first attempt.

The product behaved as designed: a rerun after a complete promotion is safe
and changed nothing it should not have; all three databases were promoted
once, and the data and markers are intact. The run is not clean because a
state-changing step ran twice and its first log was lost; the agent's list of
the commands it ran also did not match the logs, which show the guide's
commands. Fixes: `product` refuses a step whose log exists and stamps its
start time; `failover` runs in the background like `rebuild-standby`, and
the guide says never to start it again when a tool gives up waiting; it
prints `promote skipped` instead of `promote done` when it skips; and
`report` flags `"promoted_now": false`.

`acceptance.py report full` built `REPORT.md`: 104 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and every log it names
and nothing else. The operator's reviewer checked the 57 product logs' exit
status and JSON, found `"promoted_now": false`, and read `06-6`, `06-10`,
`06-11`, `08-9`, `08-10`, `11-4`, the log timestamps and the promotion
record on `.108`.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Both VM firewalls off; both VMs rolled back to `clean-agent` (links up, `onboot` 0); clean-host PASS, distinct machine IDs; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install `{"changed": true}`; HTTPS rule; services READY; CA `81:71:B8:57:…`; headers; Chromium Todo, Notes and SSO passed, none skipped; markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for todo, notes and keycloak; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | DR and quarantine tools `true` then `false`; helper READY; profile; baseline open; firewall on; client SSH open, client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; IPv6 check; shutdown, links down, isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5 on `.108`; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed from the client and from `.108`; `app_dr.py preflight`; app-ops trusted, `recovery.yaml`, HTTPS rule; `failover` twice (below): the logged second run skipped the promotion and finished deploy, backup, services, login-page and users, `{"changed": true, "promoted_now": false}`, address `192.168.0.108`, CA `1B:4F:F5:9A:…`, `next` says a browser confirms the login; status; `f\|off`; the guide's rolled-back write probes; markers |
| Application recovery | Services, CA `1B:4F:F5:9A:…` (the one `failover` printed), headers, browser tests; markers ID 41; `deploy-promoted-application` repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` (done by `failover`), later `false` again; `f\|off\|on\|1h` for all three; base backups; before and after rows inserted; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving |
| Rebuild | C9.10 in order: firewall on, isolated start, STOPPED, links up, SSH from `.108`, `.102`→`.108:22` blocked, services stopped; replication rules switched; SSH pinned; replication exception; wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 (only PostgreSQL) then VM 108, one at a time: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; backups 237/237/243 MiB, WAL 64 MiB each, 15048 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `77634b57-8e4e-42ad-bd45-c0679d75963e`; VM 107 (`.102`,
todo-primary) database-only standby streaming over TLS, boot
`8577e482-4d7d-41f6-a079-55dd4d9a6ecd`. Markers 3, 4, 5, 41, 42, 43 and 44
in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` and
`python3-pyyaml` installed on both VMs (documented prerequisites); the first
C9.13 sudo refusal check skipped; the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file.
