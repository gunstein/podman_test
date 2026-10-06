# Oracle Linux acceptance without an agent — 2026-10-06 (run 46)

**Clean pass:** `c5f4a595fff87e98b225ab795ca33e15733b9748`.
Run `2026-10-07-run-46`, the first run with no agent: the operator ran
`acceptance.py run` ([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)) and typed
the sudo password for the client trust before `03-4a`; sudo still held it
before `07-5`. CI was green on the revision. It accepts:

- **`acceptance.py run`** (`fbeb364`, `c5f4a59`). It ran C1a's readiness
  line into `logs/00-readiness.log` (`READY for the agent run.`, `exit=0`),
  made and at the end removed the testuser password file, ran every step of
  `docs/ACCEPTANCE-AGENT.md` C9 in order through `step` with one `STEP ...:
  PASS` line each, waited for the five background steps (build, bootstrap,
  failover, rebuild, reseed), and ran the guide's C9.4 client trust for
  `.102` and then `.108`, each checked by `/etc/hosts` and trusted HTTPS for
  both names (`client trust for ...: done`). An earlier attempt the same day
  stopped before its first step because `run` took Part A's readiness line,
  which writes no log; `c5f4a59` fixed that, and no step had run.
- **Every comment check as an expectation** (`67a7536`): the 15 product
  lines a person or agent used to judge now state it after `→` and `step`
  judged them all: empty IPv6 output, three standby roles with no lag, zero
  failed archive attempts (`08-3`, `08-15`), three verified base backups,
  three archived restore points, the cleanup's counts, the refusal messages,
  `NRestarts` only 0, and `source_state=clean` in both VERSION files (`02-5`,
  `02-6`).

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 65 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 28 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 45, with an
agent: 5 min 18 s). The whole run took 32 min 57 s first start to last end,
33 min 36 s in steps and 42 s between them (run 45: 42 min 46 s, 9 min 54 s
between): without an agent, almost no time goes between steps.

The product evidence matches run 45's ([record](ACCEPTANCE-9174f6c.md)):
nightly backup and restore on `.102` (todo `base-20261006T181816Z`, notes
`…181817Z`, keycloak `…181818Z`, `row_gone` 1); bootstrap streaming over TLS
1.3; every `check monitor ... ok` ready to take over with bundle
`c5f4a595fff8`; `failover` `{"changed": true, "promoted_now": true}`; `06-15`
failing as required; Todo restored to `acceptance_before_after` from
`base-20261006T183256Z` and Notes to `2026-10-06T18:33:06Z` from
`base-20261006T183257Z`, each holding only row 42; one nightly backup on
`.108`; `rebuild-standby` `true`; the reseed refusing the wrong name and then
`{"changed": true}`, the same slots streaming at 0 lag from new positions
(`0/12…`, `0/13…`); markers 3, 4, 5, 41, 44 and 45; backups 267/283/279 MiB,
14850 MiB free; `NRestarts` 0, 0, 0.

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `cfaa41ff-87eb-4576-ab2a-d4d8bac6c55c`, CA
`E4:14:08:75:31:21:26:98:CB:41:BB:C3:C5:51:B0:F1:47:C8:4D:71:C6:C9:D0:E3:F7:17:57:A2:5C:F7:E4:A5`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag, boot `6e31b7b9-83d2-4ce2-b409-51462c60d341`, Proxmox
firewall off, `onboot` 0.

## Deviations

Expected, which do not change the verdict: the first C9.13 sudo refusal
check skipped (`04-4`); the A3 lab sudoers file and a Proxmox API token; the
testuser password in a tmpfs file, removed at the end. The readiness check
warned about the old firewall rules on VM 107 (replaced by `05-7`) and VM 108
(backlog K3), as before. With no agent there is no `run-record.md` or
`FINAL-REPORT.md`; `REPORT.md` and the logs are the whole record.
