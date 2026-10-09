# Oracle Linux acceptance without an agent — 2026-10-09 (run 2026-10-09-run-3)

**Clean pass:** `3fba6a793b781618874a29a6ed8346bff3bb6095`.
Run `2026-10-09-run-3`: the operator ran `acceptance.py run`
([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)). It accepts **S5 phase 1**
(backlog S5, `2f79392`..`3fba6a7`): the installer's seven workloads in one
ordered table (`apps.workloads()`), the shared resources named directly, one
naming rule for every app's Kube YAML (`todo-postgres.yaml`,
`todo-config.yaml`, `todo-app.yaml`, which an install now writes and whose
old names it removes) and `install.install()` in named steps. Behaviour is
meant to be the same; the run shows it is, step for step, against run 2.

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 65 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

What S5 phase 1 touches, and what the run shows of it: the offline install
from the bundle's renamed files (`03-2`, `{"changed": true}`, every
`generated/target/quadlet/*.kube` checked `OK`) and the install again
changing nothing (`03-13`, `{"changed": false}`); the services in their
order on the primary (`03-4`, `03-12e`, `03-14`), after reboots (`03-10`,
`07-12`, `08-13`, `10-4`) and after a restore (`03-12c`, then `row_gone` 1);
`failover`'s steps (`06-10`: promote, deploy, backup, services, login page,
users, `{"changed": true, "promoted_now": true}`); `deploy-promoted` again
unchanged (`07-11`); `rebuild-standby` and the reseed (`09-10`, `09-13c`),
which take the database pods from the same table.

Time: "Failover (G3): 3 min 32 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 2: 3 min
31 s). The whole run took 37 min 02 s first start to last end, 37 min 19 s
in steps (background steps overlap) and 1 min 02 s between them.

The product evidence matches run 2's ([record](ACCEPTANCE-ce02176.md)):
the nightly backup and restore on `.102` (todo `base-20261009T194748Z`, notes
`…194749Z`, keycloak `…194750Z`) with `nginx certificate (local mode, Podman
secrets): valid 396 more days`; bootstrap streaming over TLS 1.3; every
`check monitor ... ok` ready to take over with bundle `3fba6a793b78`, 7 image
archives and all 13 DR secrets; `06-15` failing as required; Todo restored
to `acceptance_before_after` from `base-20261009T200235Z` and Notes to
`2026-10-09T20:02:45Z` from `base-20261009T200237Z`, each holding only row
42; one nightly backup on `.108`; the reseed refusing the wrong name and then
`{"changed": true}`, the same slots streaming at 0 lag; markers 3, 4, 5, 41,
44 and 45; backups 267/283/279 MiB, 14839 MiB free; `NRestarts` 0, 0, 0.
CAs: `CE:3F:AB:EF:…` on `.102` (`03-5`, `03-11`, `05-8-9d`), the promoted
host's own `07:5F:95:BD:…:BB:77:1E` on `.108` (`07-6`, `07-13`, `10-5b`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `8cbae78c-f2f3-4c04-b911-428aa584527d`; VM 107
(`.102`, todo-primary) database-only standby streaming over TLS 1.3 with 0
bytes apply lag, boot `2f5f69c8-5aa9-46cb-bb74-ddb6aba7a294`, Proxmox
firewall off, `onboot` 0.

## Deviations

None. Phase 3 had 36 s between steps (run 2: 6 min 47 s, the sudo timeout):
the operator answered both sudo prompts in time.

Expected, which do not change the verdict: the first C9.13 sudo refusal
check skipped (`04-4`); the A3 lab sudoers file and a Proxmox API token; the
testuser password in a tmpfs file, removed at the end. With no agent there is
no `run-record.md` or `FINAL-REPORT.md`; `REPORT.md` and the logs are the
whole record.

Not covered by this run: S5 phase 2 (`7e59462`, the DR code from the
workload table), the single sudo prompt (`7eb8fa3`), V1 and V2 (the
uninstall), which come after this revision; and, as in run 2, the provided
TLS mode, `tls-renew` and going back to the TLS volume (backlog T4 step 3).
