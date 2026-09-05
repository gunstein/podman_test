# Oracle Linux acceptance — 2026-09-05

**Unchanged-revision PASS:** `688a0f67d190cd48dc6a8e4cfbedba66a89a5e24`.
Both snapshots were reset with approval. Delivered packages reported this clean
revision; the checkout remained unchanged. CI was confirmed green by the operator.
Evidence came from assistant checks and operator-supplied Proxmox output.
Direct DR tools/playbooks were used; no runner completion state is claimed.

| Phase | Evidence |
|---|---|
| Initial deployment | ok53/changed16; repeat ok40/changed0; failed0 |
| Initial application | Two trusted Chromium tests, real Keycloak, marker ID2 and reboot persistence |
| Standby bootstrap | Primary ok67/changed11, standby ok36/changed11; failed0; streaming async lag0, read-only, reboot passed |
| DR tools | ok17/changed6; repeat ok15/changed0 |
| Quarantine | Explicit security consent; zero-process STOPPED, IPv4/IPv6 rules and connection checks, isolated boot and restoration |
| Promotion | Explicit approval; VM107 stopped, onboot0, link_down1, no HA; preflight lag0; writable promotion |
| Application recovery | ok40/changed7; repeat ok35/changed0; trusted Chromium, marker ID4, reboot |
| Backup | ok41/changed7; repeat ok30/changed0; verified backup and isolated PITR |
| Rebuild | Approved reseed; preflight both ok9/changed0; VM107 ok53/changed14, VM108 ok34/changed3; failed0; authenticated marker ID7 replicated |
| Final boots | VM107 checked before VM108 reboot; cluster checks and two trusted Chromium tests passed |

Backup `base-20260905T180013Z` was verified again after final reboot.
PITR target `m15_before_after` at `0/6002A10`: network none, recovery/paused/
read-only `t|t|on`; restored ID5 but not ID6, live retained both. Approved cleanup
removed only disposable restore resources. Post-reboot point
`acceptance_688a0f6_after_reboot` archived at `0/8009278`.

Final VM108 (.108, todo-standby) is writable primary; boot
`83298977-3f07-4051-9078-3d7ea3220061`. VM107 (.102, todo-primary) is database-only
read-only standby; boot `add23511-39c1-4608-bc58-58bc30447bef`.
Both retained marker IDs 2,4,5,6,7. Final streaming async lag0, active reserved
slot, matching standby LSNs `0/D002BB8`, no failed units, app NRestarts0,
valid nginx configuration and trusted readiness/issuer. CA SHA-256 remained
`5B:BA:87:EE:95:52:2F:15:7C:3E:FD:7F:FF:98:97:3A:F0:EC:42:85:CC:6A:BD:78:03:AE:70:41:AC:FE:55:F5`.
Archive on, timeout1h, zero failures; base52MiB, WAL177MiB, approximately16GiB free.
Backup is on-VM, not off-host protection.

Early checks saw transient healthcheck failures, OIDC503 and replication not
yet connected; fresh checks passed without intervention. Isolated DHCP boots
produced the documented bind-address failure; the helper preserved failure
evidence and verified no processes. A paste problem required a fresh terminal;
SSH preparation needed Ansible pipelining. No source fixes, security bypasses
or manual rebuild repairs were needed; not every intermediate check passed.

Keep VM107 quarantine with management SSH and narrow outbound .108:5432 access.
Do not restart its old writable role or repeat destructive rebuild. No automatic
reset or legacy retirement follows this verdict. Later runtime changes require
their own validation; this result applies to the exact tested revision.
