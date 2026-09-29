# Oracle Linux acceptance with app-ops — 2026-09-28 (run 25)

**Clean pass:** `24b32ee647a52affbebf02cec3145c77f9f9add4`.
Run `2026-09-28-app-ops-25`, executed by a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, the command list run through
`deploy/scripts/lab/acceptance.py`. It accepts R1-R3 from the code review of
`9627adb`:

- R1: `render()` replaces the whole output directory; a Kube secret that
  differs from its Podman secret is refused (the second install, the standby
  bootstrap, `failover`'s deploy step, the deploy repeat and the rebuild all
  went through the check); a failed PITR cleanup keeps the original error;
  `values.yaml` is checked; every `App` is built with keyword arguments.
- R2: `REPLICATED_DATABASES` holds only `Database`s and `describe()` is gone;
  the DR secret copy, every app-ops loop over the group, PITR and the reseed
  ran through the renamed code.
- R3: `app_dr.py` and `app_backup.py` found both packages only in
  `/opt/todo/lib`.

It is also the first run with the guide's fixed lines that read the PITR
backup names from `08-4` (run 23 stopped there, see below).

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
| Initial deployment | Install `{"changed": true}`; HTTPS rule; services READY; CA `E4:BD:4E:6E:…`; headers; Chromium Todo, Notes and SSO passed, none skipped; markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for todo, notes and keycloak; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | DR and quarantine tools `true` then `false`; helper READY; profile; baseline open; firewall on; client SSH open, client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; IPv6 check; shutdown, links down, isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5 on `.108`; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed from the client and from `.108`; `app_dr.py preflight`; app-ops trusted, `recovery.yaml`, HTTPS rule; `failover` once, in the background (start 22:13:53): promote, deploy, backup, services, login-page and users each done, `{"changed": true, "promoted_now": true}`, address `192.168.0.108`, CA `73:7E:01:EE:…`, `next` says a browser confirms the login; status; `f\|off`; the guide's rolled-back write probes; markers |
| Application recovery | Services, CA `73:7E:01:EE:…` (the one `failover` printed), headers, browser tests; markers ID 41; `deploy-promoted-application` repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` (done by `failover`), later `false` again; `f\|off\|on\|1h` for all three; base backups; before and after rows inserted; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving |
| Rebuild | C9.10 in order: firewall on, isolated start, STOPPED, links up, SSH from `.108`, `.102`→`.108:22` blocked, services stopped; replication rules switched; SSH pinned; replication exception; wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 (only PostgreSQL) then VM 108, one at a time: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; backups 221/221/227 MiB, WAL 64 MiB each, 15096 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `5194ff67-b13c-4409-88cb-78cad3e56da4`, CA `73:7E:01:EE:…`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag, boot `7210eb4f-d44a-4a58-a675-e3798a9816fc`. Markers
3, 4, 5, 41, 42, 43 and 44 in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` and
`python3-pyyaml` installed on both VMs (documented prerequisites); the first
C9.13 sudo refusal check skipped; the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file.

Observations: in `06-10-failover.log` the JSON result comes before the last
progress line, because stdout and stderr travel over SSH separately. In
`10-3-cluster-status.log`, right after the standby's reboot, `receive_lsn`
for todo and notes is lower than `replay_lsn`, as in run 22: PostgreSQL
reports the received position from the start of the WAL segment after a
restart. Lag was 0, and `10-6` shows them equal again.

## Runs 23 and 24 on the way

Recorded from the agents' own reports; their logs were not reviewed.

- **Run 23**, `68eece4`: stopped in phase 8. Phases 1-7 and the first half of
  phase 8 passed (72 tool steps, all PASS), with the same R1-R3 code as run
  25 apart from the guide. The guide asked the agent to put the backup names
  from `08-4` into `08-9` and `08-10` by hand; the agent filled in an empty
  value, and `app_backup.py` refused the command before any restore
  (`argument --backup: expected one argument`, exit 2). It then went on
  through `08-15` instead of stopping at once. A guide defect, no product
  defect. Fixed in `24b32ee`: two fixed lines read the names from the `08-4`
  log, and a test refuses any placeholder in a command line.
- **Run 24**, `24b32ee`: stopped in phases 1-3. The agent did not run the
  guide's commands as written: it replaced `01-7`/`01-8`
  (`dnf install python3-jinja2 python3-pyyaml`) with an import check, which
  fails on the clean snapshot, and `03-1` failed on a changed path. It went
  on past the first failure, and before the run it stopped the operator's
  own services on the client and started VM 107. The same agent session had
  run run 23. An agent defect; no product or guide change. Run 25 used a new
  agent session, and the kickoff now says to run every command character for
  character, to stop at the first failed product command, and not to change
  anything on the client outside the run folder.
