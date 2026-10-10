# Oracle Linux acceptance without an agent — 2026-10-10 (run 2026-10-10-run-51)

**Clean pass:** `8079d06d319d5187ce20f0fd356ab80465cf7ccb`, on
`feature/platform`. Run `2026-10-10-run-51`: the operator ran
`acceptance.py run` ([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)); CI was
green on the revision. It accepts platform phases 3 and 3b
([plan](../PLATFORM-PLAN.md), section 10) on top of run 2026-10-10-run-50
([record](ACCEPTANCE-425f6b3.md)):

- **Phase 3, the model from YAML.** Both packages were built from
  `platform.yaml` and `examples/<app>/app.yaml` (`02-1`, `02-2`), with no
  `values.yaml` and no list of apps in the code; the install, failover,
  rebuild and reseed ran from them unchanged (`03-2`, `06-10`, `09-10`,
  `09-13c`). The HTTPS port has one source: `03-2` published 8443 from the
  bundle, and the reinstall stayed unchanged (`03-13`).
- **Phase 3b, scripts read the model.** `preflight.sh` took its ports from
  the bundle (`03-1u1`, `03-2`, `03-13`: "Preflight checks passed");
  `wait-ready.sh` got its pods, containers and hostnames from its callers,
  the lab tool (every `check services` and `do reboot`) and failover's
  `services` step (`06-10`); the lab tool's firewall rules, closed ports and
  client trust took their ports and hostnames from `platform.yaml` (`03-3`,
  `04-8`, `05-7`, `06-4`, `06-5`, `09-6a`, `09-6b`, `09-8`).

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 71 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 39 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 50: 3 min
33 s). The whole run took 36 min 31 s first start to last end, 34 s of it
between steps.

The product evidence matches run 50's: the nightly backup and restore on
`.102` (todo `base-20261010T133619Z`, notes `…133620Z`, keycloak
`…133622Z`, `row_gone` 1) with `nginx certificate (local mode, Podman
secrets): valid 396 more days`; bootstrap streaming over TLS 1.3; every
`check monitor ... ok` ready to take over with bundle `8079d06d319d`, 7 image
archives and all 13 DR secrets; `06-15` failing as required; Todo restored
to `acceptance_before_after` from `base-20261010T135150Z` and Notes to
`2026-10-10T13:51:59Z` from `base-20261010T135151Z`, each holding only row
42; one nightly backup on `.108`; the reseed refusing the wrong name and then
`{"changed": true}`, the same slots streaming at 0 lag; markers 3, 4, 5, 41,
44 and 45; backups 267/283/279 MiB, 14839 MiB free; `NRestarts` 0, 0, 0.
CAs: `78:AF:B4:8A:…` on `.102` (`03-5`, `03-11`, `05-8-9d`), the promoted
host's own `EB:1F:AA:38:…:AD:00:37:9B` on `.108` (`07-6`, `07-13`, `10-5b`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `d6877570-c785-4d62-997b-1cac00260d7a`; VM 107
(`.102`, todo-primary) database-only standby streaming over TLS 1.3 with 0
bytes apply lag, boot `109eb9fa-0a2d-4fac-85c7-a4c2fb41332e`, Proxmox
firewall off, `onboot` 0.

## Deviations

None. Expected, which do not change the verdict: the readiness WARN on the
volatile journal (L4, until K1); the first C9.13 sudo refusal check skipped
(`04-4`); the A3 lab sudoers file and a Proxmox API token; the testuser
password in a tmpfs file, removed at the end. With no agent there is no
`run-record.md` or `FINAL-REPORT.md`; `REPORT.md` and the logs are the whole
record.

Not covered by this run: the provided TLS mode, `tls-renew` and going back
to the TLS volume (backlog T4 step 3); a platform other than this
checkout's `platform.yaml`, for example a third app (phases 4 and 5).
