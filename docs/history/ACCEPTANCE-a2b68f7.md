# Oracle Linux acceptance with app-ops — 2026-10-04 (run 36)

**Clean pass:** `a2b68f792347b93684244a90455430c2e650adf1`.
Run `2026-10-04-app-ops-36`, a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, every step through `acceptance.py step`,
with the guide unchanged since run 35. CI was green on the revision. It
accepts, since `b9a9180` (run 35):

- **The readiness wait on the served hostnames** (`a2b68f7`).
  `app_backup.py configure` now waits for `/ready` on the public hostnames the
  host recorded. It ran inside `failover` (`06-10`, every step done,
  `{"changed": true, "promoted_now": true}`) and as `configure-backup`, twice
  `false` (`08-1`, `08-12`); archiving held through the reboots
  (`f|off|on|1h` in `08-2`, `08-14`, `10-5a`) and `cluster-status` reported it
  healthy with 0 historical failures (`09-11d`, `10-3`, `10-6`).
- **The installer without its unused commands** (`a2b68f7`):
  `install-workload`, `configure-clients` and `services` are gone; install
  `true`, second install `false` (`03-2`, `03-13`), and every DR step that
  imports the installer passed.
- **The comment review** (`b361422`, `37dc7c7`): the rendered target files
  are byte for byte the same; the backend, frontend and proxy images, whose
  sources gained comments, were built fresh and ran in every phase, with the
  browser tests passing in phases 3, 7 and 11.

`acceptance.py report full` built `REPORT.md`: 115 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
compared with the guide at that revision, every step and log it names and
nothing else; "Needs attention: Nothing". 61 product logs, each with the
expected exit and JSON. The operator ran only the two client trust scripts.
This record is based on that `REPORT.md` and the agent's final report; the
individual logs were not read separately for it.

The phases and their evidence match run 35's
([record](ACCEPTANCE-b9a9180.md)) step for step: single-host nightly backup
and restore on `.102` (todo `base-20261004T065101Z`, notes `…065102Z`,
keycloak `…065103Z`, restore `{"changed": true}`, markers kept), bootstrap
streaming over TLS 1.3, the DR check passing on both hosts and failing after
the fence as required (`06-15`), quarantine rehearsal, fence, failover,
application recovery, PITR (rows 42 and 43), one nightly backup on `.108`
(todo `base-20261004T072113Z`, notes `…072115Z`, keycloak `…072117Z`),
rebuild, the DR check passing again, and both final reboots; backups
235/235/247 MiB, WAL 64 MiB each, 15046 MiB free (`10-8`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `0ff3859b-19e1-421b-8931-ccad17c2806b`, CA
`87:3C:A3:61:D0:59:F5:7B:2D:18:D0:04:13:4C:18:5F:7E:6B:39:63:EC:15:49:F0:B3:B0:58:3C:73:98:33:D7`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag and its DR check timer, boot
`3c445c50-fed3-43e2-876f-a709781d2178`. Markers 3, 4, 5, 41 and 44 and the
PITR rows 42 and 43 in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` still
installed on both VMs (backlog D9); the first C9.13 sudo refusal check skipped
(`04-4`); the operator ran the client trust scripts (`CLIENT_SUDO: no`); the
A3 lab sudoers file, a Proxmox API token instead of the node Shell, and the
testuser password in a tmpfs file, removed at the end.

Observations, as in run 35: Podman's event lines in the timer services'
journals, SSH's post-quantum warning, and the quarantine stop's warning that
`todo-postgres.service` remained failed after a start with every link down
(`05-8-6`); the services were stopped as required (`05-8-7c`).
