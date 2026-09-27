# Oracle Linux acceptance with app-ops — 2026-09-27 (run 11)

**Repaired functional pass:** `8d625a48abae3656370d0d551e4bff1a9986ac39`.
Run `2026-09-26-app-ops-11`, executed by an agent under
`docs/ACCEPTANCE-AGENT.md` Part C. Both packages reported this clean revision
(`source_state=clean`), and the checkout stayed clean throughout. No source was
changed. Every functional gate passed, including the three features this
revision is the first to exercise on real VMs: replication over TLS, the
nginx security headers and the Content-Security-Policy fix for Notes login.

The agent reported a CLEAN PASS. It is not one, for three reasons found by the
operator's reviewer in the logs:

1. **The quarantine rehearsal ran twice.** In the first attempt
   (`05-step5-rehearsal-proofs.log`, 06:12), sub-step 3 found HTTPS from the
   client to `.102` still working right after the VM firewall was enabled and
   logged `ERROR: HTTPS should have been blocked!`. C9.6 says STOP on any
   unexpected result. The agent instead ran the whole rehearsal again with a
   10-second wait after enabling the firewall (`05-step5-rehearsal.log`,
   06:17), where every proof passed. The draft record did not mention the
   first attempt. Cause: the Proxmox firewall service applies a changed
   `enable` flag on its next cycle, about every 10 seconds.
2. **Phase 10 has one log for eight numbered steps.** `10-reboots.log` shows
   both reboots, one at a time, active services and a fresh `cluster-status`
   after each. It has no evidence for step 5 (`nginx -t`, `f|off|on|1h`,
   backups, CA) or step 8 (backup and WAL size, free disk). Step 7 is only
   covered by the phase 11 checks. The agent guide's C9.11 summarised phase 10
   in one line, which invited this.
3. **The draft record had a wrong value.** It gave the phase 3 CA fingerprint
   as `05:32:BF:E5:…:C0:CA`; the phase 3 logs show `B0:88:7B:E3:…:18:6D`.

The record below was written from the run folder's draft record, its log
listing and these logs, read by the operator's reviewer: all `01-*`,
`03-step3`, `03-step4`, `03-step7`, `03-step8`, `03-step9`, `04-step6`,
`04-step7`, both `05-step5` logs, `06-fence`, `06-ports-closed`, `06-step1`,
`06-step4`, `06-step5`, `07-step1`, `07-step3`, `07-step4`, `07-step6`, all
`08-*`, all twelve `09-step*`, `10-reboots` and `11-final-verification`.

| Phase | Evidence |
|---|---|
| Clean-host evidence | Both VMs rolled back to `clean-agent`; no containers, volumes, secrets or Quadlet directory; SELinux Enforcing; OpenSSL 3.5.5; distinct machine IDs; `python3-jinja2` installed with `dnf` on both (documented prerequisite) |
| Build/stage | Offline and operations packages built from this revision; `SHA256SUMS` verified on both VMs |
| Initial deployment | Seven services, `nginx -t`, health and readiness for both apps, issuer `https://todo.test:8443/auth/realms/todo`. Headers from the client without `-k`: HSTS on all three URLs, and on both app hostnames `connect-src 'self' https://todo.test:8443`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`; Keycloak kept its own `Referrer-Policy: no-referrer`. Chromium Todo 2, Notes 2, SSO 1 passed, 0 skipped. Markers ID3. After the VM 107 reboot, secret IDs and CA `B0:88:7B:E3:…:18:6D` unchanged; repeat `install.sh` `{"changed": false}` |
| Standby bootstrap | `replication-status` twice. `pg_stat_replication` joined with `pg_stat_ssl` on `.102`: `streaming`, `async`, `ssl = t`, `TLSv1.3` for todo, notes and keycloak, slots active. First C9.13 sudo refusal check skipped and logged |
| Quarantine rehearsal | Second attempt: baseline reachable; firewall on; client SSH works, client HTTPS times out, `.108` SSH works, `.108` to `.102:5432` and `.102` to `.108:22` blocked, no global IPv6; shutdown, `link_down=1`, start, helper `STOPPED` (exit 0); link up, all seven units inactive with zero PIDs, no containers; firewall off, reboot; seven services, trusted HTTPS, streaming over TLS with zero lag. See repair 1 for the first attempt |
| Fence and promote | Markers ID5 read on `.108`. `pve_lab.py fence 107`: `status stopped`, `onboot 0`, `ha not managed`, `link_down=1`. `ports-closed.sh`: `CLOSED` from the client and from `.108`. Preflight passed; promotion completed; `f\|off` on all three databases; rolled-back write probes |
| Application recovery | app-ops trusted on `.108`, `recovery.yaml`, HTTPS rule; `deploy-promoted-application` `changed: true` then `false`; `nginx -t`; headers as in phase 3; Chromium 5 passed, 0 skipped; markers ID41; after the VM 108 reboot seven services and CA `79:1C:C7:D0:…:23:DB` unchanged |
| Backup/PITR | `configure-backup` `changed: true`, later `false`; `archive_timeout` 1h, zero failures; base backups `base-20260927T044442Z` (todo), `…044444Z` (notes), `…044446Z` (keycloak); restore point `acceptance_before_after` on all three. Todo and Notes restored views held only row 42, live held 42 and 43, `recovery\|paused\|read_only = t\|t\|on`, network `none`; only restore resources removed. After the VM 108 reboot both apps ready and archiving healthy |
| Rebuild | C9.10 steps 1-12 in order, one log each. Step 3 helper `STOPPED` (exit 0) with warnings: `notes-postgres` and `keycloak-postgres` failed, because they could not bind `192.168.0.102` while the link was down (expected, `PROXMOX-QUARANTINE.md`). Wrong confirmation refused; `preflight-standby-rebuild` `{"changed": false}`; `rebuild-standby` once, `{"changed": true}`. Ports 5432-5434 connect (rc 0); `cluster-status` streaming, async, `*_rebuilt_standby` slots active, zero lag, `.102` in recovery and read-only; TLS check on `.108`: `ssl = t`, `TLSv1.3` for all three. Markers ID44 read on `.102`. Quarantine lifted (`enable=0`, `onboot=0`) |
| Final boots | VM 107 then VM 108, `cluster-status` after each: streaming, zero lag, archiving healthy. Steps 5, 7 and 8 without their own evidence (repair 2) |
| Final sign-off | Chromium 2 + 2 + 1 passed, 0 skipped; all seven markers equal on both VMs; CA `79:1C:C7:D0:…:23:DB` matches; `NRestarts=0` for `todo-app`, `notes-app` and `shared-proxy`; no failed user units on either VM |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup. VM 107 (`.102`, todo-primary) database-only standby streaming over TLS.
Markers 3 (phase 3), 4 (phase 4), 5 (phase 6), 41 (phase 7), 42 and 43
(phase 8, before and after the restore point) and 44 (phase 9) on both.
Promoted-host CA SHA-256
`79:1C:C7:D0:EB:F4:DC:FF:3A:BE:9E:A6:BC:0B:D1:6A:9C:CD:D8:4C:9A:D8:8B:A2:1C:39:A2:BB:21:6D:23:DB`
(initial-host CA
`B0:88:7B:E3:3E:9F:8A:36:C8:76:F7:0C:08:AE:C1:7C:32:25:41:88:84:15:59:D3:CE:7B:15:BF:00:84:18:6D`
retired at failover).

## Deviations

The repairs, which set the verdict:

- Phase 5: the rehearsal was rerun after an unexpected result in sub-step 3
  instead of stopping (see above). The rerun changed nothing destructive and
  the second attempt passed every proof.
- Phase 10: steps 5, 7 and 8 have no evidence of their own.
- The draft record's phase 3 CA fingerprint did not match the logs.

What changed afterwards: C9.6 sub-step 2 and C9.10 step 8 now wait 20 seconds
after a Proxmox firewall change, C9.11 lists phase 10's eight steps with one
log each and the exact checks for steps 5 and 8, and the draft record must take
every value from a log and list every rerun.

Expected environment deviations (C7):

- `python3-jinja2` missing in the `clean-agent` snapshots, installed on both VMs.
- The first C9.13 sudo refusal check skipped, as the agent guide requires.
- The operator set the client's `/etc/hosts` and CA trust (`CLIENT_SUDO: no`).
- The A3 lab sudoers file, a Proxmox API token instead of the node Shell, and
  the testuser password in a tmpfs file.

Observations, not deviations:

- Keycloak lockout and password policy (H1) were applied by the installer, but
  no log reads the realm settings back.
- From the client, `ports-closed.sh` printed `No route to host` for three ports
  of the fenced VM: ARP fails for a stopped VM, so no host answered.
- Two response headers appear twice: `X-Content-Type-Options` on app pages
  (the frontend container sets it as well) and `Strict-Transport-Security` on
  `/auth/` (Keycloak sends its own with `includeSubDomains`). Browsers use the
  first HSTS header; the duplicates are harmless.
- No `run-record.md` was written; the draft record served as the run record.
