# Oracle Linux acceptance without an agent — 2026-10-09 (run 2026-10-09-run-2)

**Clean pass:** `ce02176a8cd88d27d2251f2252a81f0f5e95500a`.
Run `2026-10-09-run-2`: the operator ran `acceptance.py run`
([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)). CI was green on the
revision. It accepts, for the first time in the lab:

- **nginx's TLS files as Podman secrets** (`d3485d7`, docs/TLS.md "nginx's
  TLS files as Podman secrets"), in local mode. The install made the demo CA
  and nginx's certificate as host-local Podman secrets and nginx served them
  read-only from the Kube secret `todo-kube-proxy-tls-secret`: the client
  trusted CA `E8:7B:CD:56:…:12:AF:B1:EF` on `.102` (`03-5`), the same after
  a reboot (`03-11`) and after the quarantine rehearsal (`05-8-9d`); the
  install again changed nothing (`03-13`, `{"changed": false}`); the nightly
  run reported `nginx certificate (local mode, Podman secrets): valid 396
  more days` (`03-12a`). After the failover, `deploy-promoted` gave the
  promoted host its own demo CA as secrets, `04:19:B9:DC:…:CB:4C:8D:69`
  (`06-10` reports it with `client_trust: required`, as T4 expects), which the
  client trusted (`07-6`) and which held after two reboots (`07-13`,
  `10-5b`).
- **The provided TLS mode's code in the package** (`dfd5d42` and after):
  nothing in the run uses provided mode yet (T4 step 3), but its files travel
  in the operations package, and `app_ca.py` without a shebang
  (`ce02176`) is what lets fapolicyd's hosts read them (`02-5`, `02-6`).

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 65 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 31 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 46: 3 min
28 s). The whole run took 39 min 34 s first start to last end, 33 min 44 s in
steps and 7 min 04 s between them; 6 min 47 s of that was phase 3, the
client trust stop below.

The product evidence matches run 46's ([record](ACCEPTANCE-c5f4a59.md)):
nightly backup and restore on `.102` (todo `base-20261009T160839Z`, notes
`…160840Z`, keycloak `…160841Z`, `row_gone` 1); bootstrap streaming over TLS
1.3; every `check monitor ... ok` ready to take over with bundle
`ce02176a8cd8`, 7 image archives and all 13 DR secrets; `failover`
`{"changed": true, "promoted_now": true}`; `06-15` failing as required; Todo
restored to `acceptance_before_after` from `base-20261009T162326Z` and Notes
to `2026-10-09T16:23:36Z` from `base-20261009T162327Z`, each holding only
row 42; one nightly backup on `.108`; `rebuild-standby` `true`; the reseed
refusing the wrong name and then `{"changed": true}`, the same slots
streaming at 0 lag from new positions (`0/12…`, `0/13…`); markers 3, 4, 5,
41, 44 and 45; backups 267/283/279 MiB, 14839 MiB free; `NRestarts` 0, 0, 0.

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `f1aba7c9-b5ab-46f2-9dab-50f55d7a9f46`, CA
`04:19:B9:DC:D8:AE:36:79:9E:68:C9:99:79:FD:96:3D:9A:13:5E:0C:93:47:39:5B:67:39:D7:FA:CB:4C:8D:69`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag, boot `207de10a-096c-4837-a561-9e49f299d860`, Proxmox
firewall off, `onboot` 0.

## Deviations

- **The run stopped once, at the client trust before `03-4a`, outside the
  product.** The operator's sudo password timed out (`sudo: timed out`), so
  `/etc/hosts` on the client was not changed and `client trust for
  192.168.0.102: FAILED`. Nothing on the VMs changed; the same command went
  on, ran the client trust again (`done`) and then `03-4a`. No step was run
  twice. Backlog P3 removes the prompt.
- **Before the run, on the client:** the readiness check failed on `Local
  port 8080 free`, held by an old per-container install from
  `quadlet-reference-v1` on the client itself (`todo-frontend`), which the
  operator stopped. Backlog P2 (the stop names its FAIL lines and what holds
  the port) and V2 (uninstall that variant too).
- **An earlier run of the same day, `2026-10-09-run-1` on `d3485d7`, is not
  clean:** it stopped at `02-5-verify-102`, where `sha256sum` could not read
  `deploy/scripts/app_ca.py` ("Operation not permitted"): its shebang made
  fapolicyd treat it as an untrusted script. `ce02176` removed the shebang,
  and the package test now refuses one in any packaged `.py` file. This run
  is the new revision's own, from the first step.

Expected, which do not change the verdict: the first C9.13 sudo refusal
check skipped (`04-4`); the A3 lab sudoers file and a Proxmox API token; the
testuser password in a tmpfs file, removed at the end. With no agent there is
no `run-record.md` or `FINAL-REPORT.md`; `REPORT.md` and the logs are the
whole record.

Not covered by this run: the provided TLS mode (`tls-request`, `tls-install`,
on one host and on the DR pair), `tls-renew`, and going back to the TLS
volume. Unit tests, the proxy smoke test against real Podman and CI's full
stack cover them; the lab does not yet (backlog T4 step 3).
