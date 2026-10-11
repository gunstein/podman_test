# Oracle Linux acceptance without an agent — 2026-10-11 (run 2026-10-11-run-57)

**Clean pass:** `aaf89f820e38d98c10046374e952123cc1f392d4`, on
`feature/platform`. Run `2026-10-11-run-57`: the operator ran
`acceptance.py run` ([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)); CI was
green on the revision. It accepts platform phase 5-2, database roles from
the platform and table rights from the migrations
([plan](../PLATFORM-PLAN.md), section 5.5), on top of run 2026-10-11-run-56
([record](ACCEPTANCE-502ba89.md)).

- **Roles before the app.** The install (`03-2`) made `todo_migrator`,
  `todo_app`, `notes_migrator` and `notes_app` through each database's own
  `psql` after it was healthy; each app's migration init container then
  migrated as the migrator and granted the runtime role its tables, and the
  backend served as `<app>_app`: the browser test (`03-7`) did CRUD in both
  apps and the markers were written (`03-8`).
- **Again changes nothing, and the grants survive.** The repeated install
  (`03-13`) answered `{"changed": false}`; the role setup ran again and left
  the migrations' table rights in place, as the markers read after it
  (`03-14`, the later `check markers`) show. So did the restore from the
  nightly backup (`03-12c`, `03-12f`).
- **Roles move with the database.** The standby received the roles by
  replication; after the failover (`06-10`) the promoted host's apps wrote
  through them (`07-8` browser, `07-9` markers 41), and after the rebuild and
  re-seed of the old primary (`09-10`, `09-13c`) the markers reached it
  (`09-11h`, `09-13h`). DR runs no role setup of its own: a standby is
  read-only, and its roles are the primary's.

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 71 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 37 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 56: 3 min
37 s). The whole run took 36 min 34 s first start to last end, 30 s of it
between steps.

The product evidence matches run 56's: the bundle's 9 image archives and
`image-ids.json` verified (`03-2`, `03-13`); the nightly backup and restore on
`.102` (todo `base-20261011T173735Z`, notes `…173736Z`, keycloak `…173738Z`,
`row_gone` 1); `06-15` failing as required; Todo restored to
`acceptance_before_after` and Notes to `2026-10-11T17:53:37Z`, each holding
only row 42; the rebuild and reseed refusing first and then
`{"changed": true}`, streaming at 0 lag; markers 3, 4, 5, 41, 44 and 45;
backups 267/283/279 MiB, 14783 MiB free; `NRestarts` 0, 0, 0. CAs:
`64:CC:03:B6:…` on `.102`, the promoted host's own `45:A1:16:FB:…:3D:74:66`
on `.108`.

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `bd46b3af-4809-4de3-a8c6-44a6df4d6c2c`; VM 107
(`.102`, todo-primary) database-only standby streaming over TLS 1.3 with 0
bytes apply lag, boot `d1282e6b-24e8-4044-97e9-b147304675c6`, Proxmox
firewall off, `onboot` 0.

## Deviations

None in this run. Expected, which do not change the verdict: the readiness
WARN on the volatile journal (L4, until K1); the first C9.13 sudo refusal
check skipped (`04-4`); the A3 lab sudoers file and a Proxmox API token; the
testuser password in a tmpfs file, removed at the end. With no agent there is
no `run-record.md` or `FINAL-REPORT.md`; `REPORT.md` and the logs are the
whole record.

Not covered by this run: the runtime role refused DDL and the migration
table (CI's backend job only), a role or `CONNECT` grant changed by hand and
put right by the next install (fast tests only), the development path's role
setup (CI's full-stack job), and what run 56 left out (a wrong Keycloak
client corrected, the provided TLS mode, a browser visit to Help, Help alone).

This is the last run inside phase 5: from here a full run comes once at the
end of a phase ([plan](../PLATFORM-PLAN.md), section 7), so the next one
covers 5-3, 5-4 and the third app's row through failover, backup and PITR.
