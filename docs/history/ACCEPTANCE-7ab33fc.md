# Oracle Linux acceptance with app-ops — 2026-10-05 (run 44)

**Clean pass:** `7ab33fc607cf9418f082be78e3f114739151637d`.
Run `2026-10-05-app-ops-44`, a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, every step through `acceptance.py step`,
with no step added or changed and no step call refused or repeated. CI was
green on the revision. It accepts D10 (`7ab33fc`): `app-ops reseed-standby`
copies a standby again without a failover, as for a standby that lost its
slot. It also runs `9ef0e52` (a passed step asked for again runs nothing),
which this run never needed.

- `09-13a` wrote the pair inventory on `.108` with the current roles,
  `.108` as primary and `.102` as standby.
- `09-13b` refused the wrong confirmation (`--confirm-reseed todo-standby`,
  the primary's name) with exit 1 and "nothing was changed".
- `09-13c` ran once in the background and printed `{"changed": true}` after
  1 min 32 s: `.102` proved for each database that it is a read-only
  standby that reaches `.108`, its three databases were erased and copied
  again, and all three streamed.
- The copy is new and the slots are the same: `09-13f` `cluster-status`
  shows `todo_rebuilt_standby`, `notes_rebuilt_standby` and
  `keycloak_rebuilt_standby` streaming, async, active and `reserved`, with
  0 bytes apply lag, and the standby's receive positions moved from
  `0/10000060`, `0/11000060` and `0/10000BD0` after the rebuild (`09-11d`)
  to `0/12000060`, `0/13000060` and `0/120010F8`, a fresh base backup each.
- `.102` was `t|on` for all three (`09-13d`), every standby connection
  streamed over TLS 1.3 (`09-13e`), the `phase9b` marker (Todo and Note ID
  45) reached `.102` (`09-13h`), and the DR check passed and was ready to
  take over on both hosts (`09-13i`, `09-13j`). The final reboots then kept
  it all (`10-1` to `10-7e`).

`acceptance.py report full` built `REPORT.md`: 121 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 65 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
This record rests on `EVIDENCE.md`.

Time: 186 logs from 45 min 38 s first start to last end, 33 min 06 s in
steps and 13 min 02 s between them (run 43: 47 min 46 s, 17 min 36 s
between). Phase 9 grew to 8 min 38 s with the re-seed. The slowest steps
were `04-10-bootstrap` (2 min 17 s), `09-10-rebuild` (2 min 15 s),
`06-10-failover` (2 min 04 s), `03-2-install` (1 min 44 s) and
`09-13c-reseed` (1 min 32 s).

The rest matches run 43's ([record](ACCEPTANCE-24afaf9.md)) step for step:
nightly backup and restore on `.102` (todo `base-20261005T165458Z`, notes
`…165459Z`, keycloak `…165500Z`, restore `true`, `row_gone` 1, second install
`false`); bootstrap streaming over TLS 1.3; every `check monitor ... ok` ready
to take over with bundle `7ab33fc607cf`, 7 image archives and all 13 DR
secrets; `failover` `{"changed": true, "promoted_now": true}`; `06-15` failing
as required; Todo restored to `acceptance_before_after` from
`base-20261005T171521Z` and Notes to `2026-10-05T17:15:50Z` from
`base-20261005T171523Z`, each holding only row 42; 0 failed archive attempts;
one nightly backup on `.108`; `rebuild-standby` `true`; backups
267/283/279 MiB, WAL 64/80/64 MiB, 14934 MiB free; `NRestarts` 0, 0, 0.

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `f1b2b819-5762-413c-bb70-09a96b504851`, CA
`BD:61:94:07:EB:16:F6:BF:7C:4B:C4:7D:02:39:AE:F3:21:6F:C4:E7:13:EF:C7:EB:74:57:1E:9C:27:90:06:A5`;
VM 107 (`.102`, todo-primary) database-only standby, copied twice in phase
9, streaming over TLS 1.3 with 0 bytes apply lag and its DR check timer, boot
`5703028e-fd89-41a1-a126-4b7111611987`, Proxmox firewall off, `onboot` 0.

## Deviations

Expected (C7), which do not change the verdict: the first C9.13 sudo refusal
check skipped (`04-4`); the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file, removed at the end.

Observations: after the quarantine boot `09-5a` found the PostgreSQL units
inactive rather than failed; the check accepts either. VM 108 has one old
Proxmox firewall rule without a comment, from before these runs; the agent
left it alone (rule 7), and it has no effect with that VM's firewall off
(backlog K3).
