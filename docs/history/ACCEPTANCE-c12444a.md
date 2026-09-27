# Oracle Linux acceptance with app-ops — 2026-09-27 (run 15)

**Functional pass, not clean:** `c12444a2f070b9e7f521e69e261aa774f4056875`.
Run `2026-09-27-app-ops-15`, the second full run with `acceptance.py`.
`REPORT.md` says ALL STEPS PASS: 106 steps, all PASS, the checkout clean at
every step, every product log with the expected exit status and JSON. The
product passed every gate; the operator only ran the two client trust scripts.

It is not a CLEAN PASS because the agent did not run phase 6 as written, and
said nothing about it:

- **Ports on the fenced host.** C9.7 at this revision has
  `check ports-closed 192.168.0.102 client` and
  `check ports-closed 192.168.0.102 192.168.0.108`, five ports each. The agent
  ran four `check connect ... blocked` instead (22 and 8443 from the client, 22
  and 5432 from `.108`), so 5433 and 5434 from `.108` and the database ports
  from the client were never checked. Fencing itself is proven by `do fence`
  (stopped, `onboot` 0, `link_down=1`); the port checks are the second,
  independent proof, and only part of it was done.
- **Step labels.** The three `app_dr.py` logs are `06-8-preflight`,
  `06-8-promote` and `06-8-status` instead of `06-6`, `06-7` and `06-8`.
- `run-record.md` is empty, so no reason is recorded. The kickoff said to run
  the C9 commands in order with nothing else around them, and not to add a
  command of its own.

`REPORT.md` could not see this: it checks the steps that ran, not the steps
the guide asks for. It now compares both.

The record below was written from `REPORT.md` and these logs, read by the
operator's reviewer: `06-4`, `06-8-preflight`, `06-8-promote`, `08-4`,
`08-9`, `08-10`, `08-11`, `11-4`.

| Phase | Evidence |
|---|---|
| Clean hosts, build, stage | Firewalls off, both VMs at `clean-agent` (links up, `onboot` 0), clean-host PASS; builds, transfers, verification `exit=0` |
| Initial deployment | Install `true`, HTTPS rule, services, CA `D5:8E:5E:FA:…`, headers, browser tests, markers ID 3, reboot, same CA, markers, second install `false` |
| Standby bootstrap | SSH pinned both ways; preflight refused without the rule (`exit=1`); bootstrap `true`; status `false` twice; TLSv1.3 for all three; `.108` `t\|on`; markers ID 4; `.108` reboot, TLS again |
| Quarantine rehearsal | Tools `true` then `false`; READY; profile; baseline open; firewall on (20 s); client HTTPS, `.108`→`.102:5432`, `.102`→`.108:22` blocked; isolated start, STOPPED; links up, services stopped; firewall off, reboot; `f\|off`; TLS PASS on the first try with the new wait (`05-8-9b`); CA unchanged |
| Fence and promote | Markers ID 5; `do fence 107` stopped, `onboot` 0, not HA-managed, `link_down=1`; four port checks blocked (see above); preflight, promote ("every database is writable"), status; `f\|off`; rolled-back write probes; markers |
| Application recovery | Deploy `true`, later `false`; CA `58:57:10:FD:…`; headers, browser tests; markers ID 41; reboot with same CA, `f\|off`, markers |
| Backup/PITR | `configure-backup` `true`, later `false`; `f\|off\|on\|1h`; backups `base-20260927T122413Z`, `…122414Z`, `…122416Z`; Todo and Notes restored row 42 only, live 42 and 43, `t\|t\|on`, network `none`; no restore resources left, all `-backup` volumes kept; reboot |
| Rebuild | Steps in order; STOPPED; SSH from `.108`, `.102`→`.108:22` blocked; services stopped; rules switched; SSH pinned; replication exception with 20 s wait; wrong confirmation refused; preflight `false`; rebuild `true`; ports 5432-5434 open; `cluster-status` streaming, 0 lag; `.102` `t\|on`; TLS on `.108`; markers ID 44 on `.102`; firewall off, `onboot` 0 |
| Final boots and sign-off | VM 107 then VM 108, one at a time, roles, CA, backups, `cluster-status`; headers; markers on both; backups 189/189/211 MiB, WAL 80 MiB each, 15176 MiB free; browser tests; services on both; `NRestarts` 0, 0, 0 |

Final: VM 108 (`.108`) writable primary with application and backup, boot
`ce874996-4c3e-4c0c-9740-c8f5e95d3d73`; VM 107 (`.102`) database-only standby
streaming over TLS, boot `3da808c8-8b71-4c0e-88b8-b7ff1067f685`. Markers 3, 4,
5, 41, 42, 43 and 44 in Todo and Notes on both.

Expected deviations (C7): `python3-jinja2` installed on both VMs; the first
C9.13 sudo refusal skipped; the operator ran the client trust scripts; the A3
lab sudoers file, a Proxmox API token and the testuser password in tmpfs.
