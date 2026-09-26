# Oracle Linux acceptance with app-ops — 2026-09-25/26

**Repaired functional pass:** `f1f07b5bf8b68ace72fe756b7e322a60ede43648`.
Run `2026-09-25-app-ops-5`, executed by an autonomous agent under
`docs/ACCEPTANCE-AGENT.md` Part C with `Operations tool: app-ops`. Both packages
reported this clean revision (`source_state=clean`), and the checkout stayed
unchanged. No source was changed and no Ansible command was run. Three steps did
not pass as written and needed operator decisions (see Deviations), one of them
a retry of a failed command. Every functional gate passed.

| Phase | Evidence |
|---|---|
| Clean-host evidence | Both VMs rolled back to `clean-agent`; distinct identities, Enforcing, security services active, rootless Podman 5.8.2, no Todo/Notes/Keycloak state. `python3-jinja2` missing in snapshots, installed via `dnf` (documented prerequisite) |
| Build/stage | Offline `4e55c9ca…43e8`, operations `804ae1f3…f0b1`; checksums and inner SHA256SUMS OK on both VMs |
| Initial deployment | `install.sh` `{"changed": true}`, repeat `{"changed": false}` with identical definitions, secret IDs, CA; 7 services; trusted Chromium Todo 2, Notes 2, SSO 1 passed, 0 skipped; markers ID3 (Todo, Notes); reboot persistence |
| Standby bootstrap | Refusal without firewalld rule named the rule; `preflight-standby`, `bootstrap-standby` `{"changed": true}`, `replication-status` x2 `{"changed": false}`; three DBs streaming async lag0, active slots; standby reboot passed; marker ID4 |
| DR tools | `install-dr-tool` true then false; `app_dr.py status` healthy read-only standby x3, lag0 |
| Quarantine rehearsal | `install-quarantine-tool --enable-guest-exec --enable-selinux-entrypoint` true then false; Guest Agent `check` READY; leftover `todo-quarantine-*` rules from an earlier run deleted and recreated; HTTPS/5432/outbound blocked, client SSH allowed; STOPPED (exitcode 0); restored to 7 services and streaming lag0 |
| Promotion | Marker ID5 on standby; VM107 stopped, onboot0, link_down1, no HA; all ports unreachable; preflight lag0; one promote; `f\|off` x3; rolled-back probes OK |
| Application recovery | `deploy-promoted-application` true then false; new CA trusted; Chromium 2/2/1 passed, 0 skipped; marker ID41; reboot persistence |
| Backup/PITR | `configure-backup` true then false; backups todo `base-20260925T210443Z`, notes `base-20260925T210444Z`, keycloak `base-20260925T210446Z`; restore point `acceptance_before_after`; Todo and Notes restored ID42 only, live 42+43, `t\|t\|on`, network `none`; cleanup removed only restore resources; reboot kept archive health |
| Rebuild | Stop helper STOPPED on retry; wrong confirmation refused; `preflight-standby-rebuild` false; `rebuild-standby` once `{"changed": true}`; `cluster-status` streaming async lag0, `*_rebuilt_standby` slots active; marker ID44 on rebuilt standby |
| Final boots | VM107 then VM108, one at a time; `cluster-status` after each, lag0; Chromium 2/2/1 passed, 0 skipped; `NRestarts=0`; no failed units |

Final: VM108 (.108, todo-standby) writable primary with application and
backup, boot `d6c27b0c-1b5a-423b-ab67-dc4f8021ad06`. VM107 (.102,
todo-primary) database-only standby, boot
`1d29869c-a283-4d87-95c9-fd29253a5f30`. Markers 3, 4, 5, 41, 44 on both.
Promoted-host CA SHA-256
`40:18:79:12:BE:38:6B:F7:00:AB:19:BA:78:17:23:75:93:47:A4:0A:6D:F9:23:DC:3E:74:D0:AC:E5:D0:0E:C8`
(initial-host CA `59:FC:E4:…:9A:93` retired at failover).

## Deviations

1. Phase 5, quarantine sub-step 3: `ssh` from .108 to .102 failed with "Host
   key verification failed"; .108 only pins .102 at C9.10 step 7. The firewall
   admitted TCP 22 and a matching keyscan. Operator accepted that as proof.
   Guide ordering defect.
2. Phase 9 step 3: Guest Agent `app-quarantine.sh stop` about 10 s after boot
   failed with "Failed to connect to bus" before stopping anything. The user
   manager was not up, and the helper does not wait for it. The operator
   approved one retry, which returned STOPPED. Product or guide defect.
3. Phase 9 step 8: .102 to .108:5432-5434 cannot succeed before rebuild,
   because app-ops publishes .108 on the LAN only inside `rebuild-standby`.
   The operator approved continuing. After rebuild, all three ports connected
   (rc 0). Guide defect.
4. Transient failed Keycloak `podman healthcheck run` unit right after final
   .108 boot; cleared itself within a minute.
5. Expected agent deviations: passwordless lab sudo (A3), NOPASSWD refusal
   check skipped (C9.13), Proxmox API token, tmpfs testuser password, firewall
   evidence from API listings plus connection tests.
