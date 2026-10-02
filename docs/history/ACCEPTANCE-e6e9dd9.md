# Oracle Linux acceptance with app-ops — 2026-10-02 (run 30)

**Clean pass:** `e6e9dd9789876d816a8097aec00531ff698a4573`.
Run `2026-10-02-app-ops-30`, a new agent session under `docs/ACCEPTANCE-AGENT.md`
Part C, every step through `acceptance.py step`. It accepts the pre-rendered
offline install (`179aeb2`) with real Podman and systemd, and what followed:

- The bundle carries every Kube YAML file and Quadlet unit rendered on the
  build host, with `${TARGET_EXTERNAL_HOSTNAME}` and `${TARGET_PUBLISH_ADDRESS}`,
  and `bundle.json`; `02-5` and `02-6` verified them under `SHA256SUMS` on both
  VMs. `install.sh` filled in the defaults (`todo.test`) and
  `--publish-address 192.168.0.102` with the standard library alone
  (`target_render`), and installed through the same staging: `03-2`
  `{"changed": true}`, `03-13` `{"changed": false}`. Quadlet, nginx, TLS and
  Keycloak ran from those files (services, CA, headers and browser checks
  passed before and after the reboot). Preflight no longer requires Jinja2;
  the DR steps, which still render on the hosts, are unchanged.
- Two installer fixes: an app restarts when its shared ConfigMap file
  changed, and `install-workload postgres` passes `database=`.
- The readiness check asks the build `python3` for Jinja2 and PyYAML
  (`PASS ... /usr/bin/python3`), after run 28.
- `pve_lab.tls_context()` accepts Proxmox's root CA, which has no Key Usage,
  under Python 3.13 and newer, after run 29.

The client was upgraded to Ubuntu 26.04 after run 27: the build ran on
Python 3.14.4 and the browser tests in a Python 3.14 environment.

`acceptance.py report full` built `REPORT.md`: 104 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 58 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
The operator's reviewer read `00-readiness`, the ends of `03-2` and `03-13`,
the first two lines of `06-6` and `06-10`, and the ends of `06-10`, `08-9`,
`08-10` and `11-4`.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Readiness `READY for the agent run`, build `python3` `/usr/bin/python3` with Jinja2 and PyYAML, `exit=0`; both VM firewalls off; both VMs rolled back to `clean-agent` (links up, `onboot` 0); clean-host PASS; prerequisites, both builds, transfers and verification `exit=0` |
| Initial deployment | Install from the target files `{"changed": true}`; HTTPS rule; services READY; CA `82:D8:3D:01:…`; headers; Chromium Todo, Notes and SSO passed; test user provisioned; markers ID 3; reboot, same CA, markers; second install `{"changed": false}` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`), passed after it; bootstrap `true`; status `false` twice; no secrets in the inventory; `streaming`, `t`, `TLSv1.3` for todo, notes and keycloak; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | DR and quarantine tools `true` then `false`; helper READY; profile; baseline open; firewall on; client HTTPS, `.108`→`.102:5432` and `.102`→`.108:22` timed out; IPv6 check; shutdown, links down, isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS streaming; status `false`; CA unchanged |
| Fence and fail over | Markers ID 5; `do fence 107`: stopped, `onboot` 0, not HA-managed, `link_down=1`; ports closed from the client and from `.108`; `app_dr.py preflight` with its confirmation; `failover` once (start 18:42:32) with both confirmations: promote, deploy, backup, services, login-page and users each done, `{"changed": true, "promoted_now": true}`, CA `98:BC:6A:64:…`; `f\|off`; write probes; markers |
| Application recovery | Services, CA `98:BC:6A:64:…` (the one `failover` printed), headers, browser tests; markers ID 41; deploy repeat `false`; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `false` twice; `f\|off\|on\|1h` for all three; base backups; the restores read their backup names from `08-4`; Todo and Notes restored views held only row 42, live views 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; cleanup; reboot, archiving |
| Rebuild | C9.10 in order; wrong confirmation refused (`exit=1`); preflight `false`; `rebuild-standby` once, `true`; ports 5432-5434 open from `.102`; `cluster-status` streaming, async, slots active, 0 lag, archive healthy with 0 failures; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 (only PostgreSQL) then VM 108: roles, CA unchanged, backups, `cluster-status`; headers; markers on both; backups 221/221/227 MiB, WAL 64 MiB each, 15096 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `4396d495-abd6-47b9-8b38-68f43c0320d9`, CA `98:BC:6A:64:…`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag, boot `5ea65666-a36c-491f-b324-300540bbf91e`. Markers
3, 4, 5, 41 and 44 and the PITR rows 42 and 43 in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` and
`python3-pyyaml` installed on both VMs (still needed by the DR tools); the
first C9.13 sudo refusal check skipped (`04-4`); the operator ran the client
trust scripts (`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API
token instead of the node Shell, and the testuser password in a tmpfs file.

Observations: as before, `06-10-failover.log` has the JSON before the last
progress line. New with the client's OpenSSH: SSH to the Oracle Linux 9 VMs
warns that the connection does not use a post-quantum key exchange; the
VMs' sshd does not offer one. It changes no result.

## Run 29 on the way

`e6e9dd9`'s predecessor `daf5b0c`, run `2026-10-02-app-ops-29`: stopped in the
readiness check, before phase 1, with no VM changed. Under Python 3.14 the
Proxmox API failed TLS verification: since Python 3.13 the default context
sets `VERIFY_X509_STRICT`, which rejects Proxmox's root CA for its missing Key
Usage. Fixed in `e6e9dd9` (`pve_lab.tls_context`). The agent stopped and
asked; recorded from its report.
