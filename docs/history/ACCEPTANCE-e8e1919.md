# Oracle Linux acceptance with app-ops — 2026-09-27 (run 12)

**Repaired functional pass:** `e8e1919040934c204b97daad5ff37e89368adfe1`.
Run `2026-09-27-app-ops-12`, executed by an agent under
`docs/ACCEPTANCE-AGENT.md` Part C. Both packages reported this clean revision
(`source_state=clean`), and the checkout stayed clean throughout. No source was
changed and nothing was committed or pushed. Every functional gate passed,
with replication over TLS, the security headers and Notes login confirmed a
second time on real VMs, and phase 10 now has one log per step.

The agent reported a REPAIRED FUNCTIONAL PASS, which is right, for one repair:

- **Phase 3 HTTPS rule not permanent.** The agent added the client's HTTPS
  rich rule on `.102` without `--permanent` and without a destination address
  (`03-step2-firewall.log`), although phase 3 gives the permanent command. The
  `firewall-cmd --reload` in phase 4 dropped it, and phase 4's marker creation
  failed with `ERR_ADDRESS_UNREACHABLE`. The agent stopped; with the operator's
  approval it added the permanent rule and created the markers
  (`04-step5-markers-resumed.log`).

The operator's reviewer also found in the logs, not in the agent's record:

- **Phase 6 write probe for Notes failed.** Instead of the rolled-back inserts
  phase 6 gives, the agent inserted and deleted a Todo row (committed, not
  rolled back) and tried `INSERT INTO notes (title, content)`, which failed:
  `column "content" of relation "notes" does not exist`
  (`06-step5-verify-promoted.log`). The run did not stop. Notes was shown
  writable later by the phase 7 marker (ID 40) and the phase 8 rows. The
  committed Todo probe is why Todo and Notes IDs differ by one from phase 7 on.
- **The wrong-confirmation check logged `exit=0`.** The refusal message is
  there, and `app_ops` returns 1 on every error, so the `0` is the exit status
  of the agent's pipe, not of `app_ops`. The required exit 1 is not in the log.
- **Phase 3 step 5 (browser environment) has no log**, and `00-readiness.log`
  holds only a local Python check with a broken `pytest` entry point, not the
  readiness check C1a describes.
- **Phase 3 step 9:** the first read after the reboot ran before `nginx`
  existed; after the operator's clarification it was repeated once after a
  readiness wait (`03-step9-reboot-install-resumed.log`). This is a read-only
  repeat, not a repair.
- **VM 107 ended with `onboot=1`.** C9.10 step 12 restores the value recorded
  before phase 1; the only recorded value, read before the rollback, was `0`,
  and no log shows the value after the rollback.
- **Phase 5 sub-step 3 has no IPv6 check** (`ip -6 addr show scope global`).

| Phase | Evidence |
|---|---|
| Clean-host evidence | Both VMs rolled back to `clean-agent`; distinct machine IDs; SELinux Enforcing; `python3-jinja2` installed with `dnf` on both (documented prerequisite) |
| Build/stage | Both packages built from this revision; checksums and `VERSION` verified on both VMs |
| Initial deployment | `install.sh` `changed: true`; seven services, `nginx -t`; CA `5D:9D:F2:4A:…:9A:EB` equal on client and host; headers log: HSTS on all three URLs, `connect-src 'self' https://todo.test:8443`, `DENY`, `strict-origin-when-cross-origin` on both app hostnames; Chromium 2 + 2 + 1 passed, 0 skipped; markers ID3. After the reboot: CA and secret IDs unchanged, repeat `install.sh` `changed: false` |
| Standby bootstrap | Preflight refused without the replication rule; permanent rule added; `preflight-standby` `false`, `bootstrap-standby` `true`; `replication-status` twice; TLS check: `ssl = t`, `TLSv1.3` for all three; markers ID4 after the repair; VM 108 reboot with recovery and streaming resumed |
| Quarantine rehearsal | `install-dr-tool` and `install-quarantine-tool` `true` then `false`; `READY`; profile with the replication rule disabled; sub-step 2 waited 20 s; client HTTPS, `.108` to `.102:5432` and `.102` to `.108:22` blocked while SSH worked; helper `STOPPED`; firewall off, reboot; seven services, writable, streaming, trusted HTTPS |
| Fence and promote | Markers ID5 on `.108`. `pve_lab.py fence 107`: `stopped`, `onboot 0`, `ha not managed`, `link_down=1`. `ports-closed.sh`: `CLOSED` from the client and from `.108`. Preflight passed; promotion completed; `f\|off` on all three; Notes probe failed (above) |
| Application recovery | `deploy-promoted-application` `true` then `false`; CA `1F:48:15:F6:…:FD:73`; headers as in phase 3; Chromium 5 passed, 0 skipped; markers Todo 41, Notes 40; after the VM 108 reboot seven services, `nginx -t`, `f\|off`, markers and CA unchanged |
| Backup/PITR | `configure-backup` `true`, later `false`; `archive_timeout` 1h, zero failures; `base-20260927T055333Z`, `…055335Z`, `…055337Z`; restore point on all three. Todo restored row 42 only, live 42 and 43; Notes restored 41 only, live 41 and 42; `t\|t\|on`, network `none`; only restore resources removed. After the reboot both apps ready, WAL 81M per database |
| Rebuild | C9.10 steps 1-12 in order, one log each, with the 20 s wait in step 8. Helper `STOPPED`, all units inactive, no containers; wrong confirmation refused (exit status not captured, above); `preflight-standby-rebuild` `false`; `rebuild-standby` once, `true`. Ports 5432-5434 connect; `cluster-status` streaming, async, slots active, zero lag, `.102` read-only; TLS on `.108`: `ssl = t`, `TLSv1.3` for all three; markers Todo 44, Notes 43 on `.102`. Firewall `enable=0` |
| Final boots | Phase 10 steps 1-8, one log each: VM 107 reboot, `t\|on`, only the three PostgreSQL services; `cluster-status`; VM 108 reboot; seven services, `nginx -t`, `f\|off\|on\|1h`, zero archive failures, CA unchanged, both apps ready; `cluster-status`; client HTTPS without `-k`, issuer, all markers on both VMs; backups 190M, 190M, 212M, WAL 81M each, 15G free |
| Final sign-off | Chromium 2 + 2 + 1 passed, 0 skipped; `NRestarts=0` for all seven services on `.108` and the three on `.102`; no failed user units on either VM |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `2b35249e-bbf8-48e5-98dd-c8771fb6c3c1`. VM 107 (`.102`,
todo-primary) database-only standby streaming over TLS, boot
`f5e25b7a-18eb-4cc1-8a99-aa942e996073`. Todo markers 3, 4, 5, 41, 42, 43, 44
and Notes markers 3, 4, 5, 40, 41, 42, 43 on both. Promoted-host CA SHA-256
`1F:48:15:F6:C3:3B:C1:B2:C1:C7:5D:98:CE:CF:2D:D0:C7:3E:E3:C7:AB:43:F0:6C:F5:BC:2A:45:FC:DE:FD:73`
(initial-host CA
`5D:9D:F2:4A:C3:56:02:51:9C:1E:29:70:91:F1:FF:EB:00:56:AE:C4:42:51:B4:F1:20:E4:5C:EB:C1:BF:9A:EB`
retired at failover).

## Deviations

Expected environment deviations (C7): `python3-jinja2` installed on both VMs;
the first C9.13 sudo refusal check skipped; the operator set the client's
`/etc/hosts` and CA trust (`CLIENT_SUDO: no`); the A3 lab sudoers file, a
Proxmox API token instead of the node Shell, and the testuser password in a
tmpfs file.

Observations: the user journal did not survive the hard power-off (backlog
L4); `ports-closed.sh` printed `No route to host` from the client for two
ports of the stopped VM (ARP, as in earlier runs).

## What this run shows

The product passed every functional gate twice in a row (runs 11 and 12).
What failed in both runs was the agent's own glue: commands typed differently
from the guide (a runtime-only firewall rule, a write probe with a column that
does not exist, a pipe that hides the exit status) and checks that were not
stopped on. The next improvement is therefore fewer hand-written commands in an
agent run, not more rules.
