# Oracle Linux acceptance without an agent — 2026-10-10 (run 2026-10-10-run-50)

**Clean pass:** `425f6b387cbfa5cbf81a4b951a4d7cac056ef052`, on
`feature/platform`. Run `2026-10-10-run-50`: the operator ran
`acceptance.py run` ([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)); CI was
green on the revision. It accepts platform phases 1 and 2
([plan](../PLATFORM-PLAN.md), section 10) on top of run 2026-10-10-run-1
([record](ACCEPTANCE-8ef9e83.md)):

- **Phase 1, shared names and Keycloak's own hostname.** Every shared name
  is `platform-` (`platform-backup.timer` in `03-12a`, `platform-dr-check`
  in `05-7a`, `/opt/platform` in `05-6`). Keycloak is served on `auth.test`:
  the client trust mapped `auth.test todo.test notes.test` to each serving
  VM, failover's `users` step names all three hostnames (`06-10`), and the
  browser flows logged in through Keycloak's hostname on both hosts
  (`03-7`, `07-8`, `11-1`).
- **Phase 2, one `Platform` per installation.** The bundle carried it
  (`bundle.json` format 6), the install recorded it on each host, and every
  later tool read it from there: the nightly backup and restore (`03-12a`,
  `03-12c`), the reinstall (`03-13`, unchanged), the uninstall (`03-1u4`,
  which named the old per-container install it removed), app-ops' bootstrap,
  failover, rebuild and reseed, which staged `bundle.json` for
  `app_dr_host --project-root` (`04-10`, `06-10`, `09-10`, `09-13c`), and
  the quarantine helper (`05-6`, `05-8-6`, `09-3`).

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 71 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 33 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 1: 3 min
27 s). The whole run took 35 min 39 s first start to last end, 27 s of it
between steps.

The product evidence matches run 1's: the nightly backup and restore on
`.102` (todo `base-20261010T114823Z`, notes `…114825Z`, keycloak
`…114826Z`, `row_gone` 1) with `nginx certificate (local mode, Podman
secrets): valid 396 more days`; bootstrap streaming over TLS 1.3; every
`check monitor ... ok` ready to take over with bundle `425f6b387cbf`, 7 image
archives and all 13 DR secrets; `06-15` failing as required; Todo restored
to `acceptance_before_after` from `base-20261010T120346Z` and Notes to
`2026-10-10T12:03:56Z` from `base-20261010T120348Z`, each holding only row
42; one nightly backup on `.108`; the reseed refusing the wrong name and then
`{"changed": true}`, the same slots streaming at 0 lag; markers 3, 4, 5, 41,
44 and 45; backups 267/283/279 MiB, 14839 MiB free; `NRestarts` 0, 0, 0.
CAs: `31:AE:4B:66:…` on `.102` (`03-5`, `03-11`, `05-8-9d`), the promoted
host's own `0A:B2:6F:D5:…:0F:C3:8D:D5` on `.108` (`07-6`, `07-13`, `10-5b`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `8198f908-bf99-4ead-b502-8bb321de74c9`; VM 107
(`.102`, todo-primary) database-only standby streaming over TLS 1.3 with 0
bytes apply lag, boot `0e276c6f-a476-4ce5-b299-4e388711e3d9`, Proxmox
firewall off, `onboot` 0.

## Deviations

None in this run. It is the fourth on `feature/platform` the same day; the
three before it stopped and are not clean:

- **`2026-10-10-run-47` on `88736cc` and `-run-48` on `fe49f7e`** stopped at
  `03-2-install`: `preflight.sh` found `127.0.0.1:8080` in use right after
  the uninstall in `03-1u4`, while nothing listened there. Its probe bound
  without `SO_REUSEADDR`, which Linux refuses while connections on the port
  are in TIME_WAIT, and the first install's own HTTP checks leave those for
  a minute. `fe49f7e` treated it as a lingering port forwarder and did not
  help; `b8469cd` fixed the probe (only a listener fails it now, and a
  failure prints what `ss` shows).
- **`2026-10-10-run-49` on `b8469cd`** stopped at `05-6`: the quarantine
  helper still imported `apps.services`, which phase 2 removed, and failed
  with an `ImportError` (its test faked `python3`). `425f6b3` makes it read
  the service user's platform record, and a test now runs its real Python.
  `05-6` only checks, so nothing on the VMs had changed.

Two earlier CI failures on the branch, both in tests that phase 1 had not
followed, were fixed before these runs: the browser adapter test now serves
the identity discovery (`88736cc`), and the nginx smoke test's certificate
names `auth.test` (`5d44c50`).

Expected, which do not change the verdict: the readiness WARN on the
volatile journal (L4, until K1); the first C9.13 sudo refusal check skipped
(`04-4`); the A3 lab sudoers file and a Proxmox API token; the testuser
password in a tmpfs file, removed at the end. With no agent there is no
`run-record.md` or `FINAL-REPORT.md`; `REPORT.md` and the logs are the whole
record.

Not covered by this run: the provided TLS mode, `tls-renew` and going back
to the TLS volume (backlog T4 step 3); a platform other than the registry's
(phase 3).
