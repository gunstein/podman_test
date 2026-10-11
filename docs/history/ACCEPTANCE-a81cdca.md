# Oracle Linux acceptance without an agent — 2026-10-10 (run 2026-10-10-run-53)

**Clean pass:** `a81cdca97fc224244f81bf374d6785686e43ace1`, on
`feature/platform`. Run `2026-10-10-run-53`: the operator ran
`acceptance.py run` ([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)); CI was
green on the revision. It accepts platform phases 4d, 4f-1, 4f-2 and 4f-3
([plan](../PLATFORM-PLAN.md), section 10) on top of run 2026-10-10-run-52
([record](ACCEPTANCE-41a2af0.md)), and is the first run with the static
Help app: eight pods on the serving host.

- **Phase 4d, units from the model.** Every `.kube` unit was rendered from
  one template per kind of workload, with `Requires=` and `After=` from the
  model: the install, reinstall, failover, rebuild and reseed ran from them
  (`03-2`, `03-13` `{"changed": false}`, `06-10`, `09-10`, `09-13c`).
- **Phase 4f-1, each app's own pod template.** Todo's and Notes' pods came
  from `examples/<app>/pod.yaml.j2`, checked by `pod_contract` when the
  bundle was built (`02-1`), and ran as before (browser tests `03-7`,
  `07-8`, `11-1`, every marker).
- **Phases 4f-2 and 4f-3, Help beside Todo and Notes.** The bundle carried 8
  image archives, `help-site` among them (`03-2`, every `check monitor ...
  ok`); the install and failover's deploy checked Help through its own
  `ready` and `checks`; `wait-ready.sh` waited for its container
  `help-site` (every `check services ... app`); failover named
  `help.test` among the hostnames to point at the new host (`06-10`). The
  DR group stayed todo, notes and keycloak, and the standby ran only those
  three databases (`11-3`).

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 71 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 37 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 52: 3 min
41 s). The whole run took 36 min 45 s first start to last end, 18 s of it
between steps.

The product evidence matches run 52's: the nightly backup and restore on
`.102` (todo `base-20261010T200303Z`, notes `…200305Z`, keycloak
`…200306Z`, `row_gone` 1) with `nginx certificate (local mode, Podman
secrets): valid 396 more days`; bootstrap streaming over TLS 1.3; every
`check monitor ... ok` ready to take over with bundle `a81cdca97fc2`, 8
image archives and all 13 DR secrets; `06-15` failing as required; Todo
restored to `acceptance_before_after` from `base-20261010T201855Z` and
Notes to `2026-10-10T20:19:05Z` from `base-20261010T201856Z`, each holding
only row 42; one nightly backup on `.108`; the reseed refusing the wrong name
and then `{"changed": true}`, the same slots streaming at 0 lag; markers 3,
4, 5, 41, 44 and 45; backups 267/283/279 MiB, 14782 MiB free; `NRestarts`
0, 0, 0. CAs: `15:C6:BB:93:…` on `.102` (`03-5`, `03-11`, `05-8-9d`), the
promoted host's own `D5:C2:99:52:…:36:15:28:8B` on `.108` (`07-6`, `07-13`,
`10-5b`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `68aa1051-b2b8-4e54-bd6a-6c963f452a41`; VM 107
(`.102`, todo-primary) database-only standby streaming over TLS 1.3 with 0
bytes apply lag, boot `67b22cce-4bbf-4e24-8bd6-d0fa6e313265`, Proxmox
firewall off, `onboot` 0.

## Deviations

None. Expected, which do not change the verdict: the readiness WARN on the
volatile journal (L4, until K1); the first C9.13 sudo refusal check skipped
(`04-4`); the A3 lab sudoers file and a Proxmox API token; the testuser
password in a tmpfs file, removed at the end. With no agent there is no
`run-record.md` or `FINAL-REPORT.md`; `REPORT.md` and the logs are the whole
record.

Not covered by this run: the provided TLS mode, `tls-renew` and going back
to the TLS volume (backlog T4 step 3); a browser visit to Help (the lab's
browser tests are Todo's and Notes'; Help is checked through nginx by its
own `ready` and `checks`); `NRestarts` of `help-app.service` (`11-4` names
Todo's, Notes' and nginx's); an installation of Help alone (tested in CI
only).
