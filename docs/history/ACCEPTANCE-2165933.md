# Oracle Linux acceptance with app-ops — 2026-09-26 (run 9)

**Clean pass:** `21659331aecd8867d09c2a05e296345f8915077a`.
Run `2026-09-26-app-ops-9`, executed by an agent under
`docs/ACCEPTANCE-AGENT.md` Part C with `Operations tool: app-ops`. Both packages
reported this clean revision (`source_state=clean`), and the checkout stayed
clean throughout. No source was changed, no command was retried and no Ansible
command was run. Every phase and every numbered step passed as written, each
step with its own log (96 logs).

The record below was written from the run folder's `run-record.md`, its log
listing and these logs, read by the operator's reviewer: `03-markers*.log`,
`06-fence.log`, `06-ports-closed.log`, `08-pitr.log`, all twelve
`09-step*.log`, `11-browser-tests.log` and `11-final-verification.log`.

| Phase | Evidence |
|---|---|
| Clean-host evidence | Both VMs rolled back to `clean-agent`; no containers or volumes; SELinux Enforcing; firewalld active; `python3-jinja2` installed with `dnf` on both (documented prerequisite) |
| Build/stage | Offline `68b6d3a8…c6df`, operations `eb2674c5…44f2`; both `VERSION` files show this revision and `source_state=clean`; staged and verified on both VMs |
| Initial deployment | Install, reinstall `{"changed": false}` and reboot of VM 107; trusted Chromium tests 5 passed, 0 skipped; phase 3 markers ID4 (the only marker row) |
| Standby bootstrap | SSH keys pinned; firewalld refusal proven; `bootstrap-standby`; `replication-status` twice; three databases streaming, zero lag; VM 108 reboot; markers ID4 and ID5 read on the standby; first C9.13 sudo refusal check skipped and logged |
| Quarantine rehearsal | `install-dr-tool` and `install-quarantine-tool` `changed: true` then `false`; quarantine profile; all nine sub-steps logged separately |
| Fence and promote | Phase 6 markers ID6. `pve_lab.py fence 107`: `status stopped`, `onboot 0`, `ha not managed`, `net0 ... link_down=1`. `ports-closed.sh` from the client and from `.108`: all five ports `timeout`, `CLOSED: 192.168.0.102`. Preflight, promotion, all three databases writable (`f\|off`), rolled-back write probes |
| Application recovery | Port 8443 opened on `.108`; `deploy-promoted-application` `changed: true` then `false`; new CA trusted on the client; Chromium 5 passed, 0 skipped; phase 7 markers ID40; VM 108 reboot |
| Backup/PITR | `configure-backup` twice; base backups for todo, notes and keycloak; restore point `acceptance_before_after` on all three. Todo and Notes restored views held only row 43 (before the point), live held 43 and 44, `recovery\|paused\|read_only = t\|t\|on`, network `none`; no restore container or volume before or after; VM 108 reboot |
| Rebuild | C9.10 steps 1-12 in order, one log each: VM 107 firewall on with the replication rule off; start with the link down; stop helper `STOPPED` (exit 0); link up, SSH from the client and `.108`, `.102` to `.108:22` timed out; services stopped and no containers; firewalld rules moved; SSH from `.108` to `.102`; replication rule on. `preflight-standby-rebuild` `{"changed": false}`, then `rebuild-standby` once, `{"changed": true}`. Ports 5432-5434 connect (rc 0); `cluster-status`: streaming, async, `*_rebuilt_standby` slots active, zero lag, `.102` in recovery with `transaction_read_only` true; phase 9 markers ID45 read on `.102`. Quarantine lifted (`enable=0`, `onboot=0`) |
| Final boots | VM 107 then VM 108, one at a time, `cluster-status` after each; all markers equal on both hosts |
| Final sign-off | Chromium 2 + 2 + 1 passed, 0 skipped; `NRestarts=0` for `todo-app`, `notes-app` and `shared-proxy`; no failed user units on either VM; passwordless sudo kept; no commit or push by the agent |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `cbf48720-de6c-4077-adf1-5312dbe598fa`. VM 107 (`.102`,
todo-primary) database-only standby, boot
`ec0fc8e4-c77a-4f00-b53f-0b15b596374d`. Markers 4 (phase 3), 5 (phase 4),
6 (phase 6), 40 (phase 7), 43 and 44 (phase 8, before and after the restore
point) and 45 (phase 9) on both. Promoted-host CA SHA-256
`BD:B8:4C:00:8B:A8:1A:62:C6:99:41:9D:89:AE:8A:42:6F:AA:C5:C8:BA:DE:8B:39:8F:D8:C0:F6:20:33:55:44`
(initial-host CA
`51:65:2F:55:04:F7:55:6E:97:54:13:B9:32:4E:06:50:56:88:B3:61:C4:08:70:C9:4F:BA:66:E8:BA:18:27:72`
retired at failover).

The two standby-rebuild defects that the `3fb897f` run found are fixed and
exercised here: the rebuild preflight no longer authenticates against the
primary before it publishes its endpoint (defect A), and the reseed removes
the exited containers a hard power-off leaves before it removes the data
volume (defect B); VM 107 was stopped hard in phase 6 and rebuilt without a
failure.

## Deviations

Expected environment deviations (C7), which do not change the verdict:

- `python3-jinja2` missing in the `clean-agent` snapshots, installed on both VMs.
- The first C9.13 sudo refusal check skipped, as the agent guide requires.
- Leftover `todo-quarantine-*` rules on VM 107 from earlier runs, removed and
  recreated as C9.6 step 4 describes.
- The A3 lab sudoers file, a Proxmox API token instead of the node Shell, and
  the testuser password in a tmpfs file.

Observations, not deviations:

- Phase 3's marker is ID4, not ID3 as in earlier runs. It is the only marker
  row, so the marker was created once; the browser tests before it used the
  earlier sequence values.
- The rebuilt standby's direct SQL check printed `pg_is_in_recovery = t` with a
  setting `off`; that setting is not `transaction_read_only`, which
  `cluster-status` reports as true for all three databases.
- Step 5 found no journal entries for the stopped PostgreSQL units after the
  hard power-off and restart: the user journal did not survive (backlog L4).
