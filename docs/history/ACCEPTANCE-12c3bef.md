# Oracle Linux acceptance — 2026-09-07

**Clean PASS:** `12c3befa801f6ce53e6962396935a082d9d72fab`.
Both packages reported this clean revision throughout; the checkout remained
unchanged for the whole run. Evidence came from assistant checks over SSH and
operator-supplied Proxmox node Shell output. Direct DR tools/playbooks were
used per `docs/ACCEPTANCE.md`; no runner/wrapper scripts exist in this
revision.

| Phase | Evidence |
|---|---|
| Clean-host evidence | Found and cleared a leftover Proxmox VM-level quarantine firewall on VM107 (todo-primary) from the prior 688a0f6 run, not reset by snapshot rollback; standby was clean; both re-verified clean after fix |
| Initial deployment | ok53/changed16; repeat ok40/changed0; two trusted Chromium tests, real Keycloak, marker ID2, reboot persistence, CA fingerprint stable |
| Standby bootstrap | Primary ok67/changed11, standby ok36/changed11; failed0; streaming async lag0, active reserved slot, matching LSNs, reboot passed |
| DR tools | ok17/changed6; repeat ok15/changed0; status showed healthy read-only standby, lag0 |
| Quarantine rehearsal | Guest Agent guest-exec + SELinux unconfined opt-ins explicitly approved; STOPPED confirmed (exitcode0, zero processes/containers); HTTPS blocked, SSH allowed under quarantine; fully restored to healthy streaming afterward |
| Promotion | Explicit approval; VM107 stopped, onboot0, link_down1, no HA resource; preflight lag0; promote returned `f\|off`; rolled-back write probe accepted; all markers retained |
| Application recovery | ok40/changed7; repeat ok35/changed0; new demo CA trusted (system + Chromium NSS), stable issuer, trusted Chromium x2, marker ID39, reboot persistence |
| Backup/PITR | ok41/changed7; repeat ok30/changed0; verified backup and isolated PITR; reboot preserved backup and archive health |
| Rebuild | Approved destructive reseed; preflight both ok9/changed0; VM107 ok53/changed14, VM108 ok34/changed3; failed0; authenticated `IDENTIFY_SYSTEM` preceded volume deletion; marker ID42 replicated |
| Final boots | VM107 (rebuilt standby) rebooted and checked before VM108 (current primary) reboot; cluster-status fresh both times; final trusted Chromium test 2/2 passed, 0 skipped |

Backup `base-20260907T174121Z` was created and verified on the promoted host
(todo-standby). PITR target `acceptance_before_after` at `0/6000538`: network
`none`, recovery/paused/read-only `t|t|on`; restored ID40 but not ID41, live
retained both. Approved cleanup removed only the disposable restore container
and volume; live data (todo-postgres-data) and the backup volume
(todo-postgres-backup) were unaffected.

Final VM108 (.108, todo-standby) is writable primary; boot
`544ae999-4bb1-4415-859e-ccd5a63888a7`. VM107 (.102, todo-primary) is
database-only read-only standby; boot `8a02a14a-8485-433e-b975-51e17ffb5ac2`.
Both retained marker IDs 2, 3, 4, 39, 40, 41, 42. Final streaming async
lag0, active reserved slot (`todo_rebuilt_standby`), no failed units on either
host after final boots, app `NRestarts=0`, valid nginx configuration and
trusted readiness/issuer. Promoted-host CA SHA-256:
`95:52:D3:93:7D:30:54:AA:26:00:6F:A5:79:35:AD:0D:78:30:5D:FC:CF:6A:AF:47:00:FC:E0:F5:7A:0A:87:91`
(a new CA, expected at failover; the initial-host CA
`A1:30:19:7C:AD:60:8D:DA:1F:DD:BC:6C:89:06:21:B4:F7:DD:EE:5C:D4:7F:47:44:EE:66:AF:9E:28:97:97:DD`
was retired with it). Archive on, timeout 1h, zero failures; backup volume
approximately 212 MiB, approximately 16 GiB free on `/home`. Backup is
on-VM, not off-host protection.

Deviations, all diagnosed and repaired without source changes: a Proxmox
VM-level firewall left over from the historical 688a0f6 quarantine was still
enforcing `policy_in/out=DROP` on VM107 after snapshot rollback, blocking
HTTPS and later all outbound traffic including SSH to the standby; both were
corrected by clearing/disabling the stale rules with fresh operator-reviewed
`pvesh` commands, as the run's own client-trust and standby-bootstrap phases
independently caught. Two transient `podman healthcheck run` unit failures
were observed immediately after two of the reboots (containers were still
starting); both cleared on their own once containers reported healthy,
consistent with the transient-failure note in the 688a0f6 record. A CA from
an earlier drill was already present in the Chromium NSS database under a
different nickname; it was left in place and only the current CA was added,
per the "never delete based on filename alone" rule. No source fixes,
security bypasses or manual rebuild repairs were needed.

Keep VM107 (todo-primary) database-only; keep VM107's Proxmox VM firewall
disabled to match a normal (non-quarantined) standby, since quarantine was
lifted after successful rebuild verification. Do not restart its old writable
role or repeat destructive rebuild. No automatic reset or legacy retirement
follows this verdict. Later changes to this revision require their own
validation; this result applies to the exact tested revision
`12c3befa801f6ce53e6962396935a082d9d72fab`.
