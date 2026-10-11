# Oracle Linux acceptance without an agent — 2026-10-11 (run 2026-10-11-run-54)

**Clean pass:** `27e2f9ab23f88a6dd3e6aa01290b2da7e0d5c69c`, on
`feature/platform`. Run `2026-10-11-run-54`: the operator ran
`acceptance.py run` ([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)); CI was
green on the revision. It accepts platform phases 4g, 4h and 4i
([plan](../PLATFORM-PLAN.md), section 10) on top of run 2026-10-10-run-53
([record](ACCEPTANCE-a81cdca.md)).

- **Phase 4g, the generated dependencies on the host.** Every
  `check services ... app` (`03-4`, `03-12e`, `03-14`, `07-5`, `11-2`) also
  compared each unit's `Requires=` and `After=`, as systemd loaded them, with
  the model's dependencies among the platform's own services, exactly; all
  passed, after the install, a reboot, the failover and the last reboot.
- **Phase 4h, the host record and the install's steps.** The host record
  carried its format version through the install, the reinstall (`03-13`
  `{"changed": false}`), the failover and the rebuild; the install ran
  through `Plan` and `Prepared` and each pod template was rendered and
  checked by `pod_contract.rendered`. `wait-ready.sh` asked every container
  to run and, with a health check, to be healthy (every `check services`).
- **Phase 4i, the bundle's own images.** The bundle carried 9 image archives
  and `images/image-ids.json` under `SHA256SUMS` (`03-2`, `03-13`); every
  image's ID survived `podman save --format oci-archive` and `podman load`
  (the install stops otherwise), the repeated install loaded nothing
  (`03-13` `{"changed": false}`), and the promoted host loaded the same IDs
  (`06-10`). Every `check monitor ... ok` found bundle `27e2f9ab23f8` with 9
  image archives and all 13 DR secrets.

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 71 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 37 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 53: 3 min
37 s). The whole run took 39 min 20 s first start to last end, 31 s of it
between steps.

The product evidence matches run 53's: the nightly backup and restore on
`.102` (todo `base-20261011T052123Z`, notes `…052124Z`, keycloak
`…052125Z`, `row_gone` 1) with `nginx certificate (local mode, Podman
secrets): valid 396 more days`; bootstrap streaming over TLS 1.3; `06-15`
failing as required; Todo restored to `acceptance_before_after` from
`base-20261011T053714Z` and Notes to `2026-10-11T05:37:24Z` from
`base-20261011T053716Z`, each holding only row 42; one nightly backup on
`.108`; the rebuild and the reseed refusing first and then
`{"changed": true}`, the same slots streaming at 0 lag; markers 3, 4, 5,
41, 44 and 45; backups 267/283/279 MiB, 14782 MiB free; `NRestarts` 0, 0,
0. CAs: `21:AC:B7:0F:…` on `.102` (`03-5`, `03-11`, `05-8-9d`), the
promoted host's own `53:A8:4E:05:…:6E:B7:EF` on `.108` (`07-6`, `07-13`,
`10-5b`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `46cc5f4f-d117-4d87-9d3b-08b796a63375`; VM 107
(`.102`, todo-primary) database-only standby streaming over TLS 1.3 with 0
bytes apply lag, boot `d3064a3e-6bf1-4de3-a474-ec3e59ed14d7`, Proxmox
firewall off, `onboot` 0.

## Deviations

None. Expected, which do not change the verdict: the readiness WARN on the
volatile journal (L4, until K1); the first C9.13 sudo refusal check skipped
(`04-4`); the A3 lab sudoers file and a Proxmox API token; the testuser
password in a tmpfs file, removed at the end. With no agent there is no
`run-record.md` or `FINAL-REPORT.md`; `REPORT.md` and the logs are the whole
record.

Not covered by this run: an app without a `ready` path (each example app has
one, so `checks.wait_ready`'s wait for running containers is tested in the
fast tests only); a new bundle under the same tag over an older image (the
fast tests replace it; the lab installs each bundle on a clean snapshot);
install's refusals (a record of another format, a platform leaving out a
workload, a pod field of the wrong kind), tested in the fast tests; the
provided TLS mode, `tls-renew` and going back to the TLS volume (backlog T4
step 3); a browser visit to Help; an installation of Help alone (CI only).
