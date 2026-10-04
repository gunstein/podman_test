# Oracle Linux acceptance with app-ops — 2026-10-04 (run 42)

**Repaired functional pass:** `304c70190d8218ea8c0db140150151a41c943999`.
Run `2026-10-04-app-ops-42`, a new agent session on another vendor's smaller
model, under `docs/ACCEPTANCE-AGENT.md` Part C, every step through
`acceptance.py step`. CI was green on the revision. The current clean pass
stays on `fae11ba` ([record](ACCEPTANCE-fae11ba.md)); `304c701` changed only
the acceptance tooling and documentation since then.

`acceptance.py report full` built `REPORT.md`: 115 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 61 product logs, each with the
expected exit and JSON. The record rests on `EVIDENCE.md`.

The product evidence matches run 40's step for step: nightly backup and
restore on `.102` (todo `base-20261004T191204Z`, notes `…191205Z`, keycloak
`…191207Z`, `row_gone` 1, second install `false`); bootstrap streaming over TLS
1.3; every `check monitor ... ok` ready to take over with bundle
`304c70190d82`, 7 image archives and all 13 DR secrets; `failover`
`{"changed": true, "promoted_now": true}`; `06-15` failing as required; Todo
restored to the named point and Notes to `2026-10-04T19:57:14Z` from the base
backup it chose (`base-20261004T195652Z`), each holding only row 42; 0 failed
archive attempts; `rebuild-standby` `true`; `cluster-status` streaming with 0
lag and the archive healthy; `NRestarts` 0, 0, 0.

It is "repaired", not clean, because the operator was needed. The agent
stopped three times and its tool once refused a command:

1. Phase 3, C9.4: it stopped to inspect the client's existing
   `todo-nginx-root.crt`, following the manual procedure's "after reviewing
   the existing target". The operator said the trust script is that review.
   C9.4 says so since `a920e3c`.
2. Phase 3, C6: its sandbox showed `$XDG_RUNTIME_DIR` as read-only, so the
   password file could not be written; no password was generated. The
   operator approved one retry with write access, which passed (mode 600).
3. Phase 5, after `05-7`: it stopped because the step had replaced the three
   `todo-quarantine-*` rules from an earlier run, though rule 7 at this
   revision names them as its exception. The operator pointed to it; the
   agent recorded its first reading as wrong.
4. Phase 11: its tool refused `rm -f` of the password file; it deleted the
   file with Python's `Path.unlink` and confirmed it was gone.

C6 says since `7c2a3ef`-and-later that the sandbox needs write access to
`$XDG_RUNTIME_DIR/todo-acceptance` for the whole run, and that any way to
delete the file is fine.

Final: VM 108 (`.108`) writable primary with application, WAL archive and
both timers, CA
`31:80:7F:9A:32:9D:EC:3E:20:4C:DC:5D:D9:86:CE:27:78:D4:15:24:22:8B:84:C4:A2:02:74:10:A5:32:19:3B`;
VM 107 (`.102`) database-only standby streaming over TLS 1.3 with 0 bytes
apply lag, Proxmox firewall off, `onboot` 0.
