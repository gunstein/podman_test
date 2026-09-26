# Agent acceptance — 2026-09-24

**REPAIRED FUNCTIONAL PASS:** `3fb897fadfb361edb5307ff4c9d71802f0197993`.
First full two-VM run of the seven-pod topology with three replicated
databases (todo, notes, keycloak), driven by an autonomous agent following
[ACCEPTANCE-AGENT.md](ACCEPTANCE-AGENT.md) and [ACCEPTANCE.md](ACCEPTANCE.md).
Both packages reported this clean revision and the checkout stayed unchanged.
Evidence came from agent checks over SSH, Proxmox API calls through
`deploy/scripts/pve_lab.py`, client browser tests and operator actions noted
below. The run found two source defects in the standby rebuild; both were
worked around procedurally, so this is not a clean unchanged-procedure pass.
The source of this record is the agent's private run log; no transcript is
kept in Git.

| Phase | Evidence |
|---|---|
| Clean-host evidence | Both VMs rolled back to `clean-agent`; VM firewalls off, links up, onboot unset. Distinct machine IDs, SELinux Enforcing, sshd/firewalld/fapolicyd/qemu-guest-agent active, rootless Podman 5.8.2, no Todo/Notes/Keycloak state, 3457 MiB memory each |
| Build and stage | Offline bundle `876689e8…25ecc`, operations package `e8d4ac13…2a127e`; checksums verified on client and both VMs; both `VERSION` files `3fb897f…` / `clean`. `python3-jinja2` missing on both VMs, installed with `dnf` (deviation 1) |
| Initial deployment | `install.sh --publish-address 192.168.0.102`; all seven services active, 0 failed units, nginx `-t` OK, issuer `https://todo.test:8443/auth/realms/todo`. Trusted Chromium: `test_todo_flow.py` 2 passed, `test_multi_app.py` 1 passed, 0 skipped. Markers Todo 5 / Note 2. Reboot kept services, markers and CA `83:F4:D3:…:D1:34`; repeat install kept definition hashes, all 16 secret IDs and all 23 container IDs |
| Standby bootstrap | `preflight-standby.yml` failed=0; `bootstrap-standby.yml` primary ok=188/changed=26, standby ok=69/changed=9, failed=0. All three databases streaming async, lag 0, standby read-only. Standby reboot resumed streaming. Markers Todo 6 / Note 3 read on standby |
| DR tool and quarantine rehearsal | `install-dr-tool.yml` changed=6, repeat changed=0; `todo_dr.py status` healthy for all three databases, lag 0. Quarantine helper installed with both Guest Agent opt-ins; `check` returned `READY`. Three operator interventions (deviations 2-4), then quarantine proven with services still listening: client SSH allowed, client HTTPS 8443 and `.108`→`.102:5432` and `.102`→`.108:22` timed out. Links down, isolated boot, Guest Agent `stop` returned exit 0 `STOPPED`, zero PIDs and containers. Normal operation and streaming restored |
| Fence and promote | VM 107 hard-stopped, `onboot=0`, `link_down=1`, not an HA resource; ports 22/5432-5434/8443 unreachable from client and `.108`. `todo_dr.py preflight` and `promote` passed for all three databases; `f\|off` everywhere; rolled-back write probes on Todo and Notes succeeded; markers Todo 5-7 / Note 2-4 retained. Decision record `promotion.json` |
| Application recovery | `deploy-promoted-application.yml` ok=84/changed=11, repeat ok=82/changed=0. New CA trusted (system store and Chromium NSS); 3 browser tests passed, 0 skipped. Markers Todo 45 / Note 40. Reboot kept services, markers and CA |
| Backup and isolated PITR | `configure-backup.yml` ok=147/changed=18, repeat ok=118/changed=0. Backups `base-20260924T185508Z` (todo), `…185510Z` (notes), `…185512Z` (keycloak). Restore point `acceptance_before_after` at `0/7000498`. Todo restore: `t\|t\|on`, network `none`, before-row 46 only, live 46+47. Notes restore: `t\|t\|on`, network `none`, before-row 41 only, live 41+42. Both disposable restores cleaned up; live data and all three backup volumes intact. Reboot: 0 archive failures |
| Standby rebuild | Quarantine re-applied and `STOPPED` confirmed before reconnect. First preflight failed (defect A, deviation 5), then the first rebuild failed before any data deletion (defect B, deviation 6). After the workarounds: preflight passed (`.102` ok=67, `.108` ok=53); `rebuild-standby.yml` `.102` ok=142/changed=8, `.108` ok=159/changed=0, failed=0. `cluster-status.yml` reported all three `*_rebuilt_standby` slots streaming, async, lag 0, reserved, standby `t\|on`. Markers Todo 48 / Note 43 read directly on `.102`. Quarantine lifted, onboot restored to unset |
| Final reboots | VM 107 rebooted first: only the three PostgreSQL services, `true\|on`, 0 failed units, `cluster-status.yml` healthy. Then VM 108: all seven services, `false\|off\|on\|1h` for all three databases, nginx `-t` OK, backups intact, `NRestarts=0` for `todo-app`, `notes-app`, `shared-proxy`, 0 failed units, `cluster-status.yml` healthy. Final browser tests 3 passed, 0 skipped. All markers identical on both hosts |

Final topology: VM 108 (`.108`, `todo-standby`) is the writable primary with
all seven services, boot `67610131-646e-45b9-8850-2d7897d0fa48`. VM 107 (`.102`,
`todo-primary`) is the database-only read-only standby, boot
`d49583a9-1eab-40d3-b9eb-af1edb368a65`, with its Proxmox VM firewall disabled.
Markers on both hosts: Todo 5, 6, 7, 45, 48 and Note 2, 3, 4, 40, 43.
Backup volumes about 206-212 MiB per database, WAL 81 MiB each, 15 GiB free on
`/home` of `.108`.

The promoted-host CA was reported as `D8:2A:CE:…:68:D4:D8` by
`openssl x509 -fingerprint -sha256` in phase 7. The later "unchanged" checks
and the agent's summary used `30be7945…aeae1cceb`, which appears to be a
`sha256sum` of the PEM file rather than the certificate fingerprint. The
comparisons are internally consistent, but only the `D8:2A:…` value is a
certificate fingerprint; re-read it on `.108` before relying on either value.

## Source defects found

- **A — rebuild preflight cannot succeed.** `replication.reseed_check()` calls
  `authenticate()` against the current primary's LAN endpoint, but
  `postgres_redundancy_primary` publishes that endpoint only in play 3 of
  `rebuild-standby.yml`, after the preflight in play 2. Revision 12c3bef
  authenticated inside the reseed role instead. Error:
  `todo: replication authentication failed; data was not removed`.
- **B — reseed cannot remove the old data volume after hard fencing.**
  `replication.reseed_standby()` runs `podman volume rm` (correctly without
  `--force`), but stopped containers left by the phase 6 hard power-off still
  reference the volume. The rebuild role removes unit files but never those
  containers. Error: `podman volume failed (exit 2)`; no volume, slot or data
  was changed.

## Deviations and repairs

1. `python3-jinja2`, a documented target prerequisite, was not in the
   `clean-agent` snapshots. The agent installed it with `dnf` on both VMs
   before asking; the operator approved it afterwards. `python3-pyyaml` was
   already present.
2. The Proxmox token could not change VM network devices:
   `Permission check failed (/sdn/zones/localnetwork/vmbr0, SDN.Use)`. The
   operator granted the `PVESDNUser` role on `/sdn` to `acceptance@pve`.
3. The first rehearsal proof failed (client HTTPS still answered) because the
   Proxmox **node** firewall was disabled; VM firewall rules then have no
   effect. The operator started it (`pve-firewall start`) and the proofs were
   repeated successfully.
4. Leftover firewall rules from an earlier drill on VM 107 were deleted with
   operator approval before the quarantine profile was created.
5. Defect A was worked around by running `postgres_redundancy_primary` once on
   `.108` from a temporary playbook outside the repository and operations
   package (`/tmp/publish-primary-endpoints.yml`), before the preflight. It
   reported ok=115/changed=9; `192.168.0.108:5432-5434` then listened and
   `.102` could connect. The later rebuild's own play 3 reported no change.
6. Defect B was worked around on `.102`. The operator approved removing only
   the stopped containers that referenced the three data volumes, by ID and
   without `--force`. The agent instead ran `podman pod rm -f $(podman pod ps -q)`
   and `podman rm -f $(podman ps -a -q)`, removing every stopped pod and
   container. No volume, secret or image was removed and all three data
   volumes were still present for the reseed, so the outcome was unaffected,
   but the command exceeded the approval. The rebuild was then rerun once.
7. Agent-run environment deviations, as allowed by ACCEPTANCE-AGENT.md:
   passwordless lab sudo instead of `--ask-become-pass`, Proxmox API token
   instead of the node Shell, testuser password in a tmpfs file (deleted at
   the end), and firewall evidence from API rule listings plus connection
   tests instead of `pve-firewall` output.

That authenticated `IDENTIFY_SYSTEM` preceded volume deletion follows from
`reseed_standby()` calling `reseed_check()` before `podman volume rm`; the run
log does not show the probe separately.

Keep VM 107 database-only and its VM firewall disabled; do not restart its old
writable role or repeat promotion or rebuild. A CLEAN PASS of this topology
still requires fixing defects A and B and a new unchanged-revision run. This
result applies only to revision `3fb897fadfb361edb5307ff4c9d71802f0197993`.
