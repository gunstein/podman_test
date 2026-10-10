# Oracle Linux acceptance without an agent — 2026-10-10 (run 2026-10-10-run-1)

**Clean pass:** `8ef9e830111fd78816113a2ded676a024a2cdf9d`.
Run `2026-10-10-run-1`: the operator ran `acceptance.py run`
([ACCEPTANCE-HUMAN.md](../ACCEPTANCE-HUMAN.md)); CI was green on the
revision. It accepts, on top of run 2026-10-09-run-3
([record](ACCEPTANCE-3fba6a7.md)):

- **S5 phase 2** (`7e59462`): the DR code takes the serving tier from the
  workload table. Failover's `deploy` and `backup` steps (`06-10`),
  `deploy-promoted` again unchanged (`07-11`), `configure-backup`
  unchanged (`08-1`, `08-12`), the rebuild and the reseed (`09-10`,
  `09-13c`) all passed as before.
- **One sudo prompt, at the start** (`7eb8fa3`): the client trust before
  `03-4a` and `07-5` ran without asking; phase 3 had 3 s between steps
  (run 2: 6 min 47 s, a sudo timeout).
- **D7, packages without templates** (`21b0b51`): both packages were built,
  verified (`02-5`, `02-6`) and installed from `generated/target` alone
  (`03-2`, `04-10`, `06-10`).
- **L1, one journald line per command** (`25cd28f`): the units' journals
  show it, for example `backup nightly exit=0 seconds=4.2` (`03-12a`),
  `check exit=0 seconds=2.3` (`05-7a`), `check exit=1 seconds=1.7` for the
  failed check (`06-15`) and `nightly exit=0 seconds=6.2` (`08-16`).
- **U3 and L4 in the readiness check** (`662e6ab`, `0448962`): `PASS Clock
  synchronised (NTP)` and `WARN Journal kept across reboots: volatile`, as
  expected until the clean snapshots are made again (K1).
- **E8, V1, V2, P2** ride along: the install loads every archive (`03-2`);
  nothing in the run uninstalls, takes a dev stack down or fails the
  readiness check, so their new behaviour is covered by the unit tests, not
  by this run.

`REPORT.md`: 121 steps, all PASS, none refused or unfinished; the checkout
clean at the same revision at every step; compared with the guide at that
revision, every step and log it names and nothing else; "Needs attention:
Nothing". 65 product logs, each with the expected exit and output. This
record rests on `EVIDENCE.md`.

Time: "Failover (G3): 3 min 27 s from the fence of the old primary (06-3) to
users logging in to both apps on the promoted host (07-8)" (run 3: 3 min
32 s). The whole run took 32 min 58 s first start to last end, 10 s of it
between steps, the shortest yet.

The product evidence matches run 3's: the nightly backup and restore on
`.102` (todo `base-20261010T045621Z`, notes `…045622Z`, keycloak
`…045623Z`, `row_gone` 1) with `nginx certificate (local mode, Podman
secrets): valid 396 more days`; bootstrap streaming over TLS 1.3; every
`check monitor ... ok` ready to take over with bundle `8ef9e830111f`, 7 image
archives and all 13 DR secrets; `06-15` failing as required; Todo restored
to `acceptance_before_after` from `base-20261010T051106Z` and Notes to
`2026-10-10T05:11:16Z` from `base-20261010T051108Z`, each holding only row
42; one nightly backup on `.108`; the reseed refusing the wrong name and then
`{"changed": true}`, the same slots streaming at 0 lag; markers 3, 4, 5, 41,
44 and 45; backups 267/283/279 MiB, 14839 MiB free; `NRestarts` 0, 0, 0.
CAs: `25:BA:4C:CA:…` on `.102` (`03-5`, `03-11`, `05-8-9d`), the promoted
host's own `F1:EA:14:13:…:1D:76:90` on `.108` (`07-6`, `07-13`, `10-5b`).

Final: VM 108 (`.108`, todo-standby) writable primary with application, WAL
archive and both timers, boot `63e4577f-b3a4-4eb6-b903-1d196e6394e1`; VM 107
(`.102`, todo-primary) database-only standby streaming over TLS 1.3 with 0
bytes apply lag, boot `947f4e45-c981-441a-9519-c615830349c9`, Proxmox
firewall off, `onboot` 0.

## Deviations

None. Expected, which do not change the verdict: the readiness WARN on the
volatile journal (L4, until K1); a `conmon ... <nwarn>: Failed to open
cgroups file` line in `03-12a` from a throwaway container that had already
exited; the first C9.13 sudo refusal check skipped (`04-4`); the A3 lab
sudoers file and a Proxmox API token; the testuser password in a tmpfs file,
removed at the end. With no agent there is no `run-record.md` or
`FINAL-REPORT.md`; `REPORT.md` and the logs are the whole record.

Not covered by this run: `uninstall` (V1, V2), the development `down`, a
failing readiness check (P2), the provided TLS mode, `tls-renew` and going
back to the TLS volume (backlog T4 step 3).
