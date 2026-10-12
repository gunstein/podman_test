# Oracle Linux acceptance without an agent — 2026-10-11 (run 2026-10-11-run-58)

**Clean pass:** `c11ed00db80769cc1f9e88fd1657f04a4c95e7e8`, on
`feature/platform`. The operator ran `acceptance.py run`
([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)) and supplied `EVIDENCE.md`
on 2026-10-12. [CI #412](https://github.com/gunstein/podman_test/actions/runs/38172329539)
passed on the revision. This accepts platform phases 5-3 and 5-4a–h:
app-local sources, Guestbook and its integration, the review corrections
and the four-database DR group, on top of
[run 57](ACCEPTANCE-aaf89f8.md). The topology is ten pods: Todo, Notes,
Help and Guestbook, shared nginx and Keycloak, and PostgreSQL for Todo,
Notes, Guestbook and Keycloak.

`REPORT.md`: 124 steps, all PASS, zero FAIL, REFUSED or unfinished; 72
other logs with the expected exits. Every step used the same clean
revision; the guide comparison found every step and log it names, nothing
else. Needs attention: nothing. Recomputing the report from the local run
folder on 2026-10-12 reproduced the saved report byte for byte. Separately
rechecking all 71 product command lines against the guide at `c11ed00`
passed their output expectations. No lab hosts were contacted to record
this result.

Failover (G3): **4 min 09 s**, from fencing at `06-3` to the browser check
at `07-8`, below the 30-minute goal. Total elapsed: **43 min 42 s**;
28 s between steps. The report's phrase "both apps" is a legacy label:
the multi-app browser test at this revision also exercises Guestbook.

- **Offline install and roles.** The eleven image archives verified and
  the Quadlet install passed (`03-2`); the repeated install returned
  `{"changed": false}` (`03-13`). The browser checks at `03-7`, `07-8`
  and `11-1` passed Todo, Notes and multi-app SSO, including Guestbook
  write/read/delete and real-token rejection. Guestbook uses migration
  001's SELECT/INSERT/DELETE grants; the unused UPDATE migration is absent.
- **Guestbook markers and snapshot restore.** Its recorded IDs are 2, 3,
  4, 39, 42 and 43 for phases 3, 4, 6, 7, 9 and 9b. ID and message checks
  passed on both hosts through replication, promotion, rebuild, re-seed
  and final reboots. Nightly backup on `.102` verified all four databases
  (`03-12a`); restore named all four (`03-12c`). Separate SQL assertions
  returned `row_gone = 1` for the later Todo and Guestbook rows (`03-12d`),
  while the earlier markers survived (`03-12f`).
- **Fencing and promotion of the complete group.** Port 5435 was open
  before quarantine (`05-8-1b-guestbook`) and blocked during it
  (`05-8-3d-guestbook`). Fencing recorded the old primary stopped, link
  down, onboot 0 and no HA management; closed-port checks passed from
  client and standby. Failover returned `"promoted_now": true`, and all
  four databases became writable. Rolled-back app inserts and markers
  passed. The monitor failed as required while no standby streamed
  (`06-15`), naming all four databases.
- **Backup and PITR.** All four databases archived the named restore
  point and retained WAL archiving after reboot. Todo and Guestbook
  restored to `acceptance_before_after`; Notes restored to
  `2026-10-11T21:02:10Z`. Each disposable restore container was network
  `none`, in recovery, paused and read-only; separate SQL assertions
  proved the before row present and the after row absent in the restored
  database, with both rows still in the live database. The promoted
  host's nightly job verified four backups (`08-16`), including
  Guestbook `base-20261011T210347Z`.
- **Rebuild and re-seed.** The expected refusal gates passed first;
  `09-10` and `09-13c` then returned `{"changed": true}`. All four
  rebuilt slots streamed over TLS 1.3 with zero apply lag. Port 5435 was
  explicitly open from rebuilt standby to primary (`09-11c-guestbook`).
  New Guestbook markers 42 and 43 reached the standby after each operation.

The supplied evidence lists Guestbook's `08-10b-restore-guestbook.log` as
exit 0, but its selected log excerpts include only Todo and Notes PITR.
The separate local log was read directly when recording this acceptance:
Guestbook restored `base-20261011T210200Z`, reported `t|t|on` and network
`none`, and both `rows_match` assertions returned 1. Restored data held
only ID 40 (before); live data held IDs 40 and 41 (before and after).

Final recorded state: VM 108 (`.108`, todo-standby) is the writable primary
with application, WAL archive and backup/monitor timers, boot
`d8479742-21d6-4967-abdf-01a4afafe00f`. VM 107 (`.102`, todo-primary) is
the database-only standby, boot `cbfbe06e-50c7-40ff-a1ce-3b2300733503`,
Proxmox firewall off and onboot 0. Final cluster status showed four TLS
streams, active slots and zero apply lag; both monitors, all markers,
browser tests and service checks passed. Primary free space was 14299 MiB;
Guestbook backup/WAL used 267/64 MiB. The primary's CA remained
`E4:8C:40:DE:…:DC:3B:4D` after its reboots; the original host's was
`2E:4B:EA:0D:…`.

## Deviations and limits

No run deviations. Expected lab conditions remain: volatile journal warning,
the initial sudo-refusal check skipped as documented (`04-4`), the lab
sudoers file and Proxmox API token, and the testuser password in tmpfs.
Without an agent, `FINAL-REPORT.md` and `run-record.md` are absent;
`REPORT.md` and the logs provide the evidence.

The explicit `NRestarts` probe (`11-4`) covers Todo, Notes and nginx only
(all zero); it does not prove a zero restart count for Guestbook or Help.
Runtime UPDATE/DDL/migration-table denials are proved by CI's PostgreSQL
role tests, not by this lab run. Provided TLS mode and Help-alone behaviour
remain outside this run's scope. The pre-lab understandability review was
YELLOW, with all four findings corrected in 5-4g/h; this successful lab
run does not change that review verdict. A final fresh-agent check of the
corrected phase remains to be recorded.
