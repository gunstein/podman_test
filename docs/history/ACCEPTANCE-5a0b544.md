# Oracle Linux acceptance with app-ops — 2026-09-27 (run 14)

**Functional pass, not clean:** `5a0b544cce4f954662416a7727a8e73ab1a0ee70`.
Run `2026-09-27-app-ops-14`, the first full run with `deploy/scripts/acceptance.py`
and the command-list C9 of `docs/ACCEPTANCE-AGENT.md`. The checkout was clean
at every step (recorded by every step); no source was changed, nothing was
committed or pushed, and the operator only ran the two client trust scripts.

It is not a CLEAN PASS for one reason, in the tool, not the product:

- **05-8-9b `check replication-tls` failed once.** It ran right after VM 107
  rebooted in the rehearsal, before the standby's walreceiver had reconnected,
  so `pg_stat_replication` was empty for all three databases. The same check,
  repeated a moment later under the same step, passed with `streaming`, `t`,
  `TLSv1.3` for all three. PostgreSQL reconnects on its own within seconds;
  the check should have waited, as the guide allows for replication catching
  up. It now waits up to two minutes (`c12444a`). `REPORT.md` therefore says
  NOT CLEAN.

Also noted: a log `05-1-rule-present.log` that no C9 step names, with no
output and `exit=0`: a command the agent added. The guide says to add none.

The record below was written from `REPORT.md` (105 `acceptance.py` steps: 104
PASS, 1 FAIL; 59 other logs) and these logs, read by the operator's reviewer:
`04-13`, `05-3`, `05-8-9b` (both), `06-7`, `08-4`, `08-9`, `08-10`, `08-11`,
`11-4`.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Both VM firewalls off, both VMs rolled back to `clean-agent` (links up, `onboot` 0), clean-host checks PASS (distinct machine IDs); prerequisites, builds, transfers and verification `exit=0` |
| Initial deployment | `install.sh` `{"changed": true}`; HTTPS rule running and permanent; services READY, `nginx -t`; CA `83:C1:BE:7B:…`; headers PASS; Chromium Todo, Notes and SSO passed, none skipped; markers ID 3; reboot with new boot ID, same CA, markers present; second install `{"changed": false}` |
| Standby bootstrap | app-ops trusted; `initial.yaml`; SSH pinned both ways (host keys verified); preflight refused without the rule (`exit=1`); rule added; preflight `false`, bootstrap `true`, status `false` twice; only `initial.yaml` new in the package; TLS `streaming`, `t`, `TLSv1.3` for all three; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS and markers again |
| Quarantine rehearsal | DR tool and quarantine tool `true` then `false`; `app_dr.py status` standby, 0 bytes lag (after-restart note), primary reachable; helper READY; profile with three rules, firewall off; baseline open; firewall on with 20 s wait; client SSH open, client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; no global IPv6; shutdown, links down, start, helper STOPPED; links up, services stopped, no containers; firewall off, reboot; `f\|off`; TLS after the repeat (above); status `false`; CA unchanged |
| Fence and promote | Markers ID 5 on `.108`; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed from the client and from `.108`; preflight, promote ("every database is writable"), status; `f\|off`; guide's rolled-back write probes for Todo and Notes; markers present |
| Application recovery | Trust, `recovery.yaml`, HTTPS rule; `deploy-promoted-application` `true`, later `false`; services, CA `CB:80:88:42:…`, headers, browser tests all PASS; markers ID 41; `.108` reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `true`, later `false`; `f\|off\|on\|1h` for all three; backups `base-20260927T112313Z`, `…112314Z`, `…112316Z`; no restore state before; Todo and Notes restored views held row 42 only, live views 42 and 43, `t\|t\|on`, network `none`; cleanup left no restore resources and all three `-backup` volumes; reboot, `f\|off\|on\|1h` |
| Rebuild | Steps in order: firewall on, start, helper STOPPED, links up, SSH from `.108` open, `.102`→`.108:22` blocked, services stopped; old rule removed, new rule added; SSH pinned; replication exception on with 20 s wait; wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 (the snapshot's value) |
| Final boots and sign-off | VM 107 reboot (standby: only PostgreSQL), `t\|on`, `cluster-status`; VM 108 reboot, `f\|off\|on\|1h`, CA unchanged, backups healthy, `cluster-status`; headers; markers on both; backups 189/189/211 MiB, WAL 80 MiB each, 15176 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`) writable primary with application and backup, boot
`3d781319-2922-4e00-b997-ca9a9e62f81d`; VM 107 (`.102`) database-only standby
streaming over TLS, boot `8e5bf0b2-0569-447e-993f-cb58bca80f4f`. Markers 3, 4,
5, 41, 42, 43 and 44 in Todo and Notes on both.

## Deviations

Expected (C7): `python3-jinja2` installed on both VMs; the first C9.13 sudo
refusal check skipped; the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token and the
testuser password in a tmpfs file.

## What this run shows

With the tool, the full run needed no operator decision and no hand-copied
value, and `REPORT.md` found the one weak spot by itself. Every remaining
problem in runs 13 and 14 was in the tool (quoting, a missing wait), each
fixed with a test.
