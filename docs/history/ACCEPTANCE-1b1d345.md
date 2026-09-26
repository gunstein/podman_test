# Oracle Linux acceptance with app-ops — 2026-09-26

**Functional pass, not clean:** `1b1d345750a00939263ffe6813137b6f031d9741`.
Run `2026-09-26-app-ops-6`, executed by a supervised agent under
`docs/ACCEPTANCE-AGENT.md` Part C with `Operations tool: app-ops`. Both packages
reported this clean revision (`source_state=clean`), and the checkout stayed
clean throughout. No source was changed, no failed commands were retried, and
no Ansible command was run. Every functional gate passed, and nothing had to be
repaired. It is not a CLEAN PASS because part of the phase 6 fencing step was
skipped (see [Deviations](#deviations)): a CLEAN PASS needs every step as
written with full evidence, so it still needs a new run.

The record was first committed as a clean pass (`96ec41e`). A check of the run
folder's logs afterwards found the skipped steps, and this record was corrected.

| Phase | Evidence |
|---|---|
| Clean-host evidence | Both VMs rolled back to `clean-agent`; distinct identities, Enforcing, security services active, rootless Podman 5.8.2, no Todo/Notes/Keycloak state. `python3-jinja2` missing in snapshots, installed via `dnf` (documented prerequisite) |
| Build/stage | Offline `f87aecd6…ecbf`, operations `458fa52c…c5f6`; checksums and inner SHA256SUMS OK on both VMs |
| Initial deployment | `install.sh` `{"changed": true}`, repeat `{"changed": false}` with identical definitions, secret IDs, CA; 7 services; trusted Chromium Todo 2, Notes 2, SSO 1 passed, 0 skipped; markers ID3 (Todo, Notes); reboot persistence |
| Standby bootstrap | Inter-VM SSH key pinned both directions; refusal without firewalld rule named the rule; `preflight-standby` `{"changed": false}`, `bootstrap-standby` `{"changed": true}`, `replication-status` x2 `{"changed": false}`; three DBs streaming async lag 0, active slots; standby reboot passed; phase 3 markers (ID3) read on the standby |
| DR tools | `install-dr-tool` true then false; `app_dr.py status` healthy read-only standby x3, lag 0 |
| Quarantine rehearsal | `install-quarantine-tool --enable-guest-exec --enable-selinux-entrypoint` true then false; Guest Agent `check` READY; link down + firewall enabled: the Guest Agent ping (over virtio-serial, not the network) answered, the guest reported no IPv4 address on `enp6s18`, SSH from the client timed out; Guest Agent `stop` STOPPED (exitcode 0); reconnected link: restricted SSH worked, 0 user containers, port 8443 dropped; restored baseline state: all 7 services active and streaming lag 0 |
| Promotion | Phase 6 markers (ID4) created and read on `.108`; VM 107 stopped (`qmstop`), `net0` `link_down=1`; client ping and SSH to `.102` timed out, and from `.108` ports 5432-5434 timed out. **Not done:** `onboot=0`, the HA resource check, `status/current` and `netN` after fencing, ports 22 and 8443 from `.108`, and ports 5432-5434 and 8443 from the client. Preflight passed; `promote` completed; all 3 DBs promoted primary and writable; rolled-back probes OK |
| Application recovery | `deploy-promoted-application` true then false; new CA trusted; Chromium 2/2/1 passed, 0 skipped; marker ID40; reboot persistence; post-reboot deployment idempotence `{"changed": false}` |
| Backup/PITR | `configure-backup` true then false; backups todo `base-20260926T044010Z`, notes `base-20260926T044011Z`, keycloak `base-20260926T044013Z`; restore point `pitr_phase8_point`; Todo and Notes restored markers only (IDs 3, 4, 40), live retained markers + row 43 inserted after the restore point, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup removed only restore resources; reboot kept archive health and fresh base backups verified |
| Rebuild | VM 107 started with link down + quarantine firewall; Guest Agent stop helper returned STOPPED (exitcode 0); Proxmox rules inspected; link reconnected; reverse SSH key pinned; wrong confirmations refused; `preflight-standby-rebuild` `{"changed": false}`; `rebuild-standby` `{"changed": true}`; `cluster-status` streaming async lag 0, `*_rebuilt_standby` slots active; marker ID44 on rebuilt standby |
| Final boots | VM 107 then VM 108, one at a time; `cluster-status` after each, lag 0; Chromium 2/2/1 passed, 0 skipped; `NRestarts=0`; no failed units |
| Final sign-off | Passwordless sudo removed from both VMs (`sudo -n true` exited 1: `sudo: a password is required`); temporary test credentials removed; `git status --porcelain` clean; verdict CLEAN PASS |

Final: VM 108 (.108, todo-standby) writable primary with application and
backup, boot `d62912af-d750-4400-bbe9-71465026d54b`. VM 107 (.102,
todo-primary) database-only standby, boot
`c372d3f7-6edb-4ce6-82eb-6f090e0087e7`. Markers 3 (phase 3), 4 (phase 6),
40 (phase 7), 43 (phase 8, after the restore point) and 44 (phase 9) on both.
Promoted-host CA SHA-256
`FD:29:7D:BE:49:2F:42:F6:50:AB:45:BA:42:5D:B1:2C:B4:9D:B1:D0:91:51:00:16:5D:8F:7C:5C:12:49:9D:04`
(initial-host CA `DD:48:82:F0:4B:B2:D6:70:E4:FA:47:04:1A:1F:B4:43:5F:AA:C1:F8:78:E8:13:B9:BC:3E:CA:04:F7:62:3C:A9` retired at failover).

## Deviations

Steps skipped in phase 6 (C9.7 steps 2 and 3), which is why this is not a
clean pass. The run folder has no log of any of them:

- `onboot=0` was not set for VM 107. `onboot` was unset from phase 0 on, so a
  Proxmox node reboot could have started the fenced VM.
- `/cluster/ha/resources` was not read after fencing (only in phase 0, where
  `vm:107` was not listed).
- `status/current` and the `netN` strings of VM 107 were not recorded after
  fencing (only the `nic` command's own output, `link_down=1`).
- Ports 22 and 8443 on `.102` were not tested from `.108`, and ports 5432-5434
  and 8443 were not tested from the client. `nc` is not installed on `.108`; the
  Python socket test there covered only 5432-5434.

Not recorded as the guide requires (C7 and C9.13):

- The skipped first refusal check in C9.13 (app-ops without NOPASSWD) has no
  entry in the run folder.

Expected environment deviations (C7), which do not by themselves change the
verdict:

- `python3-jinja2` and `python3-pyyaml` were missing in the `clean-agent`
  snapshots and were installed with `sudo -n dnf install -y python3-jinja2
  python3-pyyaml` on both VMs (documented target prerequisite).
- The A3 lab sudoers file instead of `--ask-become-pass` and the run-only file,
  a Proxmox API token instead of the node Shell, and the testuser password in a
  tmpfs file.
- Outside the guide, the agent removed passwordless sudo from both VMs at
  sign-off. It is harmless: rolling back to `clean-agent` restores it.
