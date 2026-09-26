# Oracle Linux acceptance with app-ops — 2026-09-26 (run 7)

**Repaired functional pass:** `0604c56e5e55bc820e0cc7cdf650b51a2846176d`.
Run `2026-09-26-app-ops-7`, executed by an agent under
`docs/ACCEPTANCE-AGENT.md` Part C with `Operations tool: app-ops`. Both packages
reported this clean revision (`source_state=clean`), and the checkout stayed
clean throughout. No source was changed and no Ansible command was run. Every
functional gate passed, and phase 6 now has full fencing evidence.

It is not a CLEAN PASS for one reason: in phase 9 the agent ran
`rebuild-standby` before C9.10 steps 4-8 (the firewall changes and the
reachability check). The command refused at its replication authentication
check, before any data was deleted, and the agent ran it a second time after
doing steps 4-8. C9.10 step 10, C8 and C2 forbid rerunning a failed destructive
command. The code worked as designed: the rebuild's own gate stopped it safely.

The record below was written from the run folder's `run-record.md`, its log
listing and these logs, read by the operator's reviewer:
`06-fence.log`, `06-ports-closed.log`, `06-promotion.log`,
`05-rehearsal-sub3.log`, `05-rehearsal-sub8.log`, `08-pitr.log`,
`09-11-cluster-status.log` and `11-final-verification.log`.

| Phase | Evidence |
|---|---|
| Clean-host evidence | Both VMs rolled back to `clean-agent`; distinct identities; `python3-jinja2` installed with `dnf` on both (documented prerequisite); a leftover VM 107 Proxmox firewall `enable=1` from an earlier run set to `0` |
| Build/stage | Offline `f87aecd6…ecbf`, operations `0bc4a04c…930f`; both `VERSION` files show this revision and `source_state=clean` |
| Initial deployment | Install, repeat install and reboot of VM 107; trusted Chromium tests 5 passed, 0 skipped; phase 3 markers ID3 |
| Standby bootstrap | SSH keys pinned; firewalld refusal; `preflight-standby`, `bootstrap-standby` `{"changed": true}`, `replication-status` twice; three databases streaming, zero lag; standby reboot; phase 4 markers ID4; first C9.13 sudo refusal check skipped and logged (`04-sudo-refusal-skip.log`) |
| Quarantine rehearsal | All nine sub-steps logged separately (`05-rehearsal-sub1` to `sub9`). Sub-step 3: SSH from the client and from `.108` worked, HTTPS from the client timed out, `.108` to `.102:5432` and `.102` to `.108:22` timed out (rc 124), no global IPv6. Sub-step 8: VM firewall `enable=0`, `net0` `link_down=0`, reboot task OK |
| Fence and promote | Phase 6 markers ID5. `pve_lab.py fence 107`: `status stopped`, `onboot 0`, `ha not managed`, `net0 ... link_down=1`. `ports-closed.sh` printed `CLOSED: 192.168.0.102` from the client and from `.108`. Preflight passed; promotion completed; all three databases promoted and writable with zero local apply lag and the old primary's endpoints unreachable |
| Application recovery | `deploy-promoted-application`; new CA trusted on the client; Chromium 5 passed, 0 skipped; phase 7 markers ID41 |
| Backup/PITR | Base backups todo `base-20260926T171311Z`, notes `base-20260926T171313Z`, keycloak `base-20260926T171315Z`; restore point `acceptance_before_after` on all three. Todo and Notes restored views held only row 42 (before the point), live held 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup left no restore container or volume. Backups persisted across the VM 108 reboot |
| Rebuild | First `rebuild-standby` too early and refused before deletion (see above). Then steps 4-8 in order, `preflight-standby-rebuild`, `rebuild-standby` `{"changed": true}`; from `.102` ports 5432-5434 on `.108` connect (rc 0); `cluster-status`: streaming, async, `*_rebuilt_standby` slots active, zero lag, `.102` in recovery and read-only; phase 9 markers ID44 |
| Final boots | VM 107 then VM 108, one at a time, `cluster-status` after each; Chromium 5 passed, 0 skipped; `NRestarts=0` for `todo-app`, `notes-app` and `shared-proxy`; no failed user units on either VM |
| Final sign-off | Passwordless sudo kept on both VMs as the kickoff asked; no commit or push by the agent; `git status --porcelain` clean |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `25974302-2dbc-4854-9be2-73dc4c47b13b`. VM 107 (`.102`,
todo-primary) database-only standby, boot
`627dffc0-1dff-4f8a-96cc-9fb0ebef7ad6`. Markers 3 (phase 3), 4 (phase 4),
5 (phase 6), 41 (phase 7), 42 and 43 (phase 8, before and after the restore
point) and 44 (phase 9). The jump from 5 to 41 is expected: sequences skip
their cached values after a promotion. Promoted-host CA SHA-256
`49:B0:C4:74:27:5B:F2:1C:00:B6:FF:1D:07:08:96:45:90:9B:C9:25:40:3F:EA:7C:E7:90:73:6E:D5:21:DA:75`
(initial-host CA
`59:69:AF:53:5A:88:25:3B:A5:CF:28:75:E1:67:82:60:C6:8C:49:C1:1C:7B:1C:DE:17:5F:17:3C:37:FF:92:CF`
retired at failover).

## Deviations

The repair, which sets the verdict:

- Phase 9: `rebuild-standby` was started after C9.10 step 3, before steps 4-8.
  It failed at the replication authentication check (`09-rebuild-standby.log`,
  19:16) because the replication path from `.102` to `.108` was still closed,
  and it deleted nothing. After steps 4-8 (`09-04` to `09-08` logs, 19:17)
  and a new preflight, it was run again and succeeded (`09-10-rebuild-standby.log`).
  The read-only `preflight-standby-rebuild` passed both times: by design it
  never contacts the current primary, whose replication endpoint is published
  only inside the rebuild.

Expected environment deviations (C7), which do not by themselves change the
verdict:

- `python3-jinja2` missing in the `clean-agent` snapshots, installed on both VMs.
- A leftover Proxmox firewall `enable=1` on VM 107 (phase 1) and leftover
  `todo-quarantine-*` rules (phase 5) from earlier runs, cleared as C9.6 step 4
  describes.
- The first C9.13 sudo refusal check skipped, as the agent guide requires.
- The A3 lab sudoers file, a Proxmox API token instead of the node Shell, and
  the testuser password in a tmpfs file.

Observation: `ports-closed.sh` labelled every failed connection that did not
time out as `refused`. From the client, two ports of the stopped VM 107 showed
`refused`; with the VM stopped and its link down, the likely cause is the
client's own `No route to host` after ARP failed, not a host answering. The
script now prints the error text instead.
