# Oracle Linux acceptance without an agent — 2026-10-11 (run 2026-10-11-run-56)

**Clean pass:** `502ba8984b60bbf6a141b9329a389dd32b2b7d85`, on
`feature/platform`. Run `2026-10-11-run-56`: the operator ran
`acceptance.py run` ([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)); CI was
green on the revision. It accepts platform phases 5-1a, 5-1b and 5-1c, the
identity contract ([plan](../PLATFORM-PLAN.md), section 10), on top of run
2026-10-11-run-54 ([record](ACCEPTANCE-27e2f9a.md)).

The run before it, `2026-10-11-run-55` on `3395a9b`, stopped at
`03-1u5-nothing-left`: since 5-1c each login app's frontend mounts its
ConfigMap, of which kube play makes a named volume, and uninstall left
`todo-backend-config` and `notes-backend-config`. `502ba89` makes uninstall
remove each app's ConfigMap volume; this run's `03-1u5` found nothing left.

- **Phase 5-1a, Keycloak's addresses in one place.** The install, the
  repeated install (`03-13` `{"changed": false}`), the promoted host's issuer
  check and the failover's login check (`06-10`, the authorization endpoint
  now read from discovery) and the nightly backup's wait (`03-12a`, `08-16`)
  ran from `keycloak.py`'s addresses.
- **Phase 5-1b, client and audience apart.** Every client was made from the
  platform's defaults (the realm import holds none), and the backends accept
  only their API audience: the lab's browser test (`03-7`, `07-8`, `11-1`)
  logged in to both apps, checked each access token's audience (`todo-api`,
  `notes-api`, never the client), and sent the real ID tokens from Keycloak to
  the API, which refused them, on the first host, after the failover and at
  the end.
- **Phase 5-1c, login settings from the config.** Each frontend read its
  client from `/config/login.json`, the one ConfigMap key its pod mounts;
  the browser test found each app's own client there and the ConfigMap's
  other keys answering 404.

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 71 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 37 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 54: 3 min
37 s). The whole run took 36 min 37 s first start to last end, 34 s of it
between steps.

The product evidence matches run 54's: the bundle's 9 image archives and
`image-ids.json` verified (`03-2`, `03-13`); the nightly backup and restore on
`.102` (todo `base-20261011T154148Z`, notes `…154149Z`, keycloak `…154150Z`,
`row_gone` 1); `06-15` failing as required; Todo restored to
`acceptance_before_after` from `base-20261011T155742Z` and Notes to
`2026-10-11T15:57:52Z`, each holding only row 42; the rebuild and reseed
refusing first and then `{"changed": true}`, streaming at 0 lag; markers 3,
4, 5, 41, 44 and 45; backups 267/283/279 MiB, 14783 MiB free; `NRestarts`
0, 0, 0. CAs: `2B:07:9A:45:…` on `.102`, the promoted host's own
`3A:A0:84:7A:…:6A:2E:45` on `.108`.

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `15a0aa3d-6e39-491c-9eda-d1af620295e9`; VM 107
(`.102`, todo-primary) database-only standby streaming over TLS 1.3 with 0
bytes apply lag, boot `2f21970a-9ea3-4442-aa4c-5ca21ddef254`, Proxmox
firewall off, `onboot` 0.

## Deviations

None in this run. Expected, which do not change the verdict: the readiness
WARN on the volatile journal (L4, until K1); the first C9.13 sudo refusal
check skipped (`04-4`); the A3 lab sudoers file and a Proxmox API token; the
testuser password in a tmpfs file, removed at the end. With no agent there is
no `run-record.md` or `FINAL-REPORT.md`; `REPORT.md` and the logs are the
whole record.

Not covered by this run: an existing Keycloak client with a wrong audience
mapper or PKCE setting being corrected (fast tests only; the lab installs on
a clean snapshot), a ConfigMap volume refreshed after `FRONTEND_LOGIN`
changes on an installed host, an app without a `ready` path, the provided TLS
mode, `tls-renew` and going back to the TLS volume (backlog T4 step 3), a
browser visit to Help, and Help alone (CI only).
