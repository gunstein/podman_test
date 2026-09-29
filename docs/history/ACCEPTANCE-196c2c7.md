# Oracle Linux acceptance with app-ops — 2026-09-29 (run 27)

**Clean pass:** `196c2c7c7dc529cd72c7a9809b64f8ca432d3243`.
Run `2026-09-29-app-ops-27`, executed by a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C. It accepts everything since `24b32ee`:

- R5: `stack.Names` holds only the naming rules; `Database` and `App` name
  their own resources and `App` forwards nothing. Every service, volume,
  secret, slot and rendered file the run used came through the new model.
- S2: paths and constants in `settings.py` (`KUBE_RUNTIME`, `TOOLS_BIN`,
  `TOOLS_LIB`, `DR_CONFIG`, `PROMOTION_RECORD`, `RPO_TARGET_SECONDS`), and
  the offline bundle named from `IMAGE_TAG`.
- S3 and L2: `commands.run` and `replication.sql()` are the one way the
  installer and the DR tools run programs and SQL; archiving, base backups
  and PITR (phase 8) and the promotion and reseed ran through them.
- S5: `StandbyGroup` and `DatabaseBackup`.
- The review fixes on the way (psql exit 2 as the only retried pause state,
  every start failure a `CommandError`), E6, E7 and E9 (tests and CI only),
  the shared PostgreSQL image prepared once, and hostnames that must be DNS
  names (`todo.test` and `notes.test` passed).

It is also the first run in which every C9 step ran through
`acceptance.py step NAME`, which runs the guide's line as written and only
after the step before it passed (runs 24 and 26 stopped on changed commands
and steps run past a failure). Each product log now holds its exact command
on line 2; `06-6` and `06-10` show both confirmations as the guide gives them.

`acceptance.py report full` built `REPORT.md`: 104 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 58 product logs, each with the
expected exit and JSON (one more than run 25: `04-4` is now a product log).
The operator ran only the two client trust scripts. The operator's reviewer
read `00-readiness`, the first two lines of `06-6` and `06-10`, and the ends
of `06-10`, `08-9`, `08-10` and `11-4`.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Readiness `READY for the agent run`, `exit=0`; both VM firewalls off; both VMs rolled back to `clean-agent` (links up, `onboot` 0); clean-host PASS, distinct machine IDs; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install `{"changed": true}`; HTTPS rule; services READY; CA `70:28:8F:65:…`; headers; Chromium Todo, Notes and SSO passed; test user provisioned (`03-4b`, `provision-user.sh`); markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true` (in the background); status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for todo, notes and keycloak; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | DR and quarantine tools `true` then `false`; helper READY; profile; baseline open; firewall on; client SSH open, client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; IPv6 check; shutdown, links down, isolated start, STOPPED (the expected warning about a failed PostgreSQL unit); links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5 on `.108`; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed from the client and from `.108`; `app_dr.py preflight` with `--confirm-primary-fenced 'todo-primary is fenced'`; app-ops trusted, `recovery.yaml`, HTTPS rule; `failover` once, in the background (start 18:33:11), with both confirmations: promote, deploy, backup, services, login-page and users each done, `{"changed": true, "promoted_now": true}`, address `192.168.0.108`, CA `AF:0F:6C:09:…`; status; `f\|off`; the guide's rolled-back write probes; markers |
| Application recovery | Services, CA `AF:0F:6C:09:…` (the one `failover` printed), headers, browser tests; markers ID 41; `deploy-promoted-application` repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` (done by `failover`), later `false` again; `f\|off\|on\|1h` for all three; base backups; before and after rows inserted; the restores read their backup names from the `08-4` log (`backup_name`); Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving |
| Rebuild | C9.10 in order: firewall on, isolated start, STOPPED, links up, SSH from `.108`, `.102`→`.108:22` blocked, services stopped; replication rules switched; SSH pinned; replication exception; wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag, archive healthy with 0 failures; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 (the value `01-3` recorded, read by `recorded_onboot`) |
| Final boots and sign-off | VM 107 (only PostgreSQL) then VM 108, one at a time: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; backups 221/221/227 MiB, WAL 64 MiB each, 15096 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `a10ae8d3-d9b3-4930-8676-7191df3a43f6`, CA `AF:0F:6C:09:…`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag, boot `22648b0a-fc51-4c2b-8890-1e4e5020c2ad`. Markers
3, 4, 5, 41 and 44 and the PITR rows 42 and 43 in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` and
`python3-pyyaml` installed on both VMs (documented prerequisites); the first
C9.13 sudo refusal check skipped (`04-4`); the operator ran the client trust
scripts (`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token
instead of the node Shell, and the testuser password in a tmpfs file.

Observations: as in runs 22 and 25, `06-10-failover.log` has the JSON result
before the last progress line (stdout and stderr travel over SSH
separately), and `10-3-cluster-status.log`, right after the standby's
reboot, shows `receive_lsn` for todo and notes below `replay_lsn`; lag was 0
and `10-6` shows them equal again.
