# Oracle Linux acceptance with app-ops — 2026-10-05 (run 43)

**Clean pass:** `24afaf9148fa5269e41fea5040e145b946127f9b`.
Run `2026-10-05-app-ops-43`, a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, every step through `acceptance.py step`,
with no step added or changed. CI was green on the revision. It accepts A1
and A2 (`24afaf9`), and with them the guide fixes since `fae11ba` (the client
writes of C9's own steps, rule 7's quarantine-rule exception, the trust
script as the review of the client CA file, the sandbox and the password
file).

- **A2, consecutive checks in one call.** Each `$A step` call that passed
  also ran the `check` lines right after it in the same block, each with its
  own log and record line, and named the next step. `REPORT.md` holds the
  same 115 steps as runs 40 to 42, every one PASS, in guide order.
- **A1, the run timed.** `REPORT.md` ends with the new Time section: 176
  logs from 47 min 46 s first start to last end, 30 min 38 s in steps and
  17 min 36 s between them (the agent and the operator). Phases 3 and 7,
  where the operator ran the client trust scripts, had the longest gaps
  (3 min 40 s and 4 min 20 s). The slowest steps were `09-10-rebuild`
  (2 min 17 s), `04-10-bootstrap` (2 min 16 s), `06-10-failover` (1 min 56 s)
  and `03-2-install` (1 min 45 s); each reboot took about a minute.

`acceptance.py report full` built `REPORT.md`: 115 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 61 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
This record rests on `EVIDENCE.md`.

The product evidence matches run 40's ([record](ACCEPTANCE-fae11ba.md)) step
for step: nightly backup and restore on `.102` (todo `base-20261005T034433Z`,
notes `…034434Z`, keycloak `…034436Z`, restore `true`, `row_gone` 1, second
install `false`); bootstrap streaming over TLS 1.3; every `check monitor ...
ok` ready to take over with bundle `24afaf9148fa`, 7 image archives and all
13 DR secrets; `failover` `{"changed": true, "promoted_now": true}`; `06-15`
failing as required; Todo restored to `acceptance_before_after` from
`base-20261005T040806Z` and Notes to `2026-10-05T04:08:27Z` from the base
backup it chose, `base-20261005T040807Z`, each holding only row 42 while the
live tables held 42 and 43; 0 failed archive attempts; one nightly backup on
`.108` (`base-20261005T041039Z`, `…041041Z`, `…041043Z`); `rebuild-standby`
`true`; `cluster-status` streaming, 0 lag, archive healthy; backups
235/251/247 MiB, WAL 64/80/64 MiB, 15030 MiB free; `NRestarts` 0, 0, 0.

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `ac067717-d9bb-4a94-a6c4-0a1baae8419e`, CA
`A6:4E:8D:20:39:1A:BD:74:33:C9:E8:A7:FB:C7:1B:53:27:51:96:36:37:78:30:DD:3D:05:5A:E2:4C:04:5F:F5`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag and its DR check timer, boot
`6a06f6e3-e4a6-4388-b87c-694b04f85245`, Proxmox firewall off, `onboot` 0.

## Deviations

Expected (C7), which do not change the verdict: the first C9.13 sudo refusal
check skipped (`04-4`); the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file, removed at the end.

Process, noted by the agent; neither ran anything or reached `record.jsonl`:

- `$A step 02-6-verify-108` twice in one command, against rule 13. The
  second call was refused because the step had passed; the agent stopped,
  and the operator said to go on.
- One `$A step 09-11e` call refused. The agent had discarded the output of
  the `09-11d` call, which had already run `09-11e` and `09-11f` as the
  checks right after it. Since `9ef0e52` a step that already passed, asked
  for again, runs nothing, says `already passed` and names the next step,
  so this is no longer a refusal.
