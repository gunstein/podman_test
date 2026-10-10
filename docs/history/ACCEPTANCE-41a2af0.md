# Oracle Linux acceptance without an agent — 2026-10-10 (run 2026-10-10-run-52)

**Clean pass:** `41a2af03930ef4da0be4fc6cf7d3b5f6e95b4685`, on
`feature/platform`. Run `2026-10-10-run-52`: the operator ran
`acceptance.py run` ([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)); CI was
green on the revision. It accepts platform phases 4a, 4b, 4c-1 and 4c-2
([plan](../PLATFORM-PLAN.md), section 10) on top of run 2026-10-10-run-51
([record](ACCEPTANCE-8079d06.md)):

- **Phase 4a, images from `app.yaml`.** The offline bundle carried the 7
  image archives built from each app's declared `context` and
  `containerfile` (`02-1`, `03-2`: every `images/*.tar` OK), and every
  `check monitor ... ok` found the bundle `41a2af03930e` with 7 image
  archives.
- **Phase 4b, nginx from routes.** The rendered nginx served every app's
  routes as before: the headers (`03-6`, `07-7`, `10-7a`), both browser
  logins through it (`03-7`, `07-8`, `11-1`) and every marker.
- **Phase 4c-1, database and login as app fields.** Todo and Notes say
  `database: true` and name their clients, so the DR group stayed todo,
  notes, keycloak (bootstrap, failover, rebuild and reseed), and Keycloak and
  its database ran as before.
- **Phase 4c-2, ready and checks from `app.yaml`.** The install ran each
  app's `ready` and `checks` after Keycloak's setup (`03-2` and `03-1u1`
  `{"changed": true}`, `03-13` `{"changed": false}`); `deploy-promoted`
  in failover did the same, after the issuer and clients (`06-10`, its
  `deploy` step); `wait-ready.sh` got `HOSTNAME/PATH` from its callers (every
  `check services` and `do reboot`, and failover's `services` step).

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 71 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 41 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 51: 3 min
39 s). The whole run took 36 min 08 s first start to last end, 36 s of it
between steps.

The product evidence matches run 51's: the nightly backup and restore on
`.102` (todo `base-20261010T170000Z`, notes `…170001Z`, keycloak
`…170002Z`, `row_gone` 1) with `nginx certificate (local mode, Podman
secrets): valid 396 more days`; bootstrap streaming over TLS 1.3; every
`check monitor ... ok` ready to take over with all 13 DR secrets; `06-15`
failing as required; Todo restored to `acceptance_before_after` from
`base-20261010T171543Z` and Notes to `2026-10-10T17:15:52Z` from
`base-20261010T171544Z`, each holding only row 42; one nightly backup on
`.108`; the reseed refusing the wrong name and then `{"changed": true}`, the
same slots streaming at 0 lag; markers 3, 4, 5, 41, 44 and 45; backups
267/283/279 MiB, 14839 MiB free; `NRestarts` 0, 0, 0. CAs: `38:2B:50:F4:…`
on `.102` (`03-5`, `03-11`, `05-8-9d`), the promoted host's own
`4F:29:53:52:…:D2:7D:3A` on `.108` (`07-6`, `07-13`, `10-5b`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `4b576667-7e78-4fe4-a18c-f2878a3bc5e3`; VM 107
(`.102`, todo-primary) database-only standby streaming over TLS 1.3 with 0
bytes apply lag, boot `c3535ec7-e898-48cb-bac1-ca81f2a4e61e`, Proxmox
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
checkout's `platform.yaml`, for example an app without a database or login,
which cannot be installed yet (phase 4f); a check's status other than 200.
