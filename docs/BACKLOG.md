# Backlog

Agreed work that waits until the two-VM acceptance run with app-ops has finished.
Changing checked code during a run would test a different revision from the one
in the kickoff message. Remove an item when its change is merged.

## Principles

The project stays simple and pedagogical (AGENTS.md). This backlog must not
turn it into a monster in code, maintenance or operation.

1. **Shrink first.** Removal and simplification come before new features.
2. **No new runtime dependencies.** Only the Python standard library, systemd,
   journald, Podman and PostgreSQL on the hosts. New tools are allowed in CI
   only.
3. **Reuse before new.** Build on what exists (app-ops commands, systemd
   timers, the DR secret synchronisation), not on new services.
4. **Less code in total.** Once Ansible and the duplication are gone, the
   project should have less code than today, new features included.
5. **Optional means optional.** Drop an optional item without regret when it
   costs more than it gives.

Each item is marked: *[simplify]* removes or unifies code; *[docs]* is
documentation or a procedure; *[config]* is a setting; *[new]* adds a
feature; *[optional]* can be dropped; *[operator]* is lab work for the
operator, not code; *[decision]* needs the owner's choice before any work.

## Order

1. A CLEAN PASS with app-ops: a clean baseline to compare against. Run 7 on
   `0604c56` reached a REPAIRED FUNCTIONAL PASS (a rerun of `rebuild-standby`
   started out of order); the rebuild preflight now checks the replication
   path, so that mistake stops in a read-only command.
2. Security: T1 (encrypted replication between the two sites), H1 (Keycloak
   brute force) and H2 (security headers).
3. Failover to Trondheim within 30 minutes (see the goal below): G1 (one
   failover command), G2 (Trondheim is ready), G3 (time it in the drill), T3
   (fencing without the Oslo hypervisor), T4 (one CA), T5 (moving the names),
   M1 (alerts), O1 (incident runbooks), G4 (the disaster drill in the lab) and
   G5 (rebuilding Oslo on new hardware).
4. What operation needs: U1 (updating a replicated pair), T6 (planned
   switchover), M2 (scheduled backups with pruning), L1 and L2 (command
   logging, failure reasons). Decide D5 (pgBackRest) before building M2 and
   D2, since it would replace both.
5. Retire Ansible (R0, R0b, 3-6) once there is a CLEAN PASS. Then decide D4
   (one database server or one per app).
6. fapolicyd (F), firewalls (W) and data checks (C).
7. DR code structure and the rest.

The real setup has two machines and no third, on separate hardware at separate
physical sites. D2 and L6 are therefore designed for two hosts that each keep
what the other would lose: each host backs up its own database copy (D2), and
each holds the other's logs (L6). Everything between them crosses a network
between sites (T1).

## Goal: Trondheim running within 30 minutes

The point of the solution: if the Oslo site is lost (for example a fire), the
service runs in Trondheim within about 30 minutes, with at most a few simple,
well-described manual steps.
[TARGET-PICTURE.md](TARGET-PICTURE.md) shows the result on one page.

With two sites and no third, failover must not start by itself. Trondheim
cannot tell "Oslo is on fire" from "the link between the cities is down"; if it
promoted itself on a broken link, both sites would take writes (split-brain),
which loses and corrupts data. Fully automatic failover needs a third witness,
such as a small cloud VM. Without one, the safe design is one human decision
("Oslo is lost", T3), after which one command does the rest.

- **G1. One failover command.** *[new]* Run in Trondheim after the decision:
  preflight, group promotion, application tier, backup configuration, and a
  final check that users can log in. Mostly a chain of existing steps
  (`app_dr.py preflight/promote`, `deploy-promoted-application`,
  `configure-backup`), stopping at the first failure. It changes DNS through
  the provider's API if there is one (T5), or prints exactly what to do.
- **G2. Trondheim is ready to take over.** *[new]* Part of the scheduled
  checks (M1): the same bundle and operations package revision as Oslo, every
  DR secret and the shared CA (T4) synchronised, the recovery inventory in
  place, and enough disk. A missing piece found during a fire is found too late.
- **G3. Time the failover in the drill.** *[new]* Acceptance measures the time
  from "Oslo declared lost" to "users log in in Trondheim", and requires under
  30 minutes. The goal is then shown, not only that failover works.
- **G4. A disaster drill in the Proxmox lab.** *[new]* A separate, shorter
  drill next to acceptance that simulates the Oslo fire realistically on the
  two lab VMs, timed as in G3:
  - *No access to Oslo after the fire.* VM 107 is killed hard (`qm stop`) while
    running; after that, nothing may be done to it, so the fencing procedure
    without the Oslo hypervisor (T3) is what gets tested.
  - *Fire while writing.* A simple loop writes rows all the time and VM 107 is
    killed in the middle; afterwards, count the rows missing in Trondheim. That
    measures the real loss (C4, T2).
  - *Distance between the cities.* `tc qdisc ... netem` on the VMs adds delay
    (for example 10 ms) and limits bandwidth, so replication, lag and reseed
    run over something like the real link. Commands only, no new software.
  - *A broken link without a fire.* Block only the traffic between the VMs
    with the Proxmox firewall while Oslo still answers a client. The procedure
    must not promote Trondheim on that basis, or, if it does, the quarantine
    must handle Oslo when the link returns.
  - *A name change like DNS.* A small DNS with a short TTL in the lab (for
    example dnsmasq on the client or the Proxmox host) that the failover
    command or the operator updates, or `/etc/hosts` as a documented stand-in.
  The lab cannot show that the sites are independent (the VMs share hardware,
  power and storage) or the real link; both come from the real setup (T2).
- **G5. Rebuild Oslo on new hardware, then move back.** *[new]* After a fire
  the old Oslo machine is gone; a new one arrives with a clean install.
  Acceptance only rebuilds the *old* primary, with its install and data, into
  a standby (phase 9). Turning a brand-new host into a standby of a promoted
  Trondheim (install from the bundle, synchronise secrets and the CA,
  replicate from Trondheim, install the DR tools) is untested. The commands
  exist (`bootstrap-standby` with Trondheim as primary), but were made for the
  first setup, whose primary was never promoted. Then move operation back to
  Oslo with a planned switchover (T6). Cover the whole path in the runbook and
  the drill: fire, Trondheim, new Oslo, back to Oslo. In the lab, roll VM 107
  back to `clean-agent` after the disaster drill, so it is a new machine.

## Between the two sites

- **T1. Encrypt replication.** *[new]* `pg_hba.conf` uses `host`, not `hostssl`,
  PostgreSQL has no TLS, and `primary_conninfo` sets no `sslmode`. SCRAM keeps
  the password off the wire, but the WAL stream (all content of all three
  databases, including Keycloak users and password hashes) travels in clear
  text. Acceptable inside one room; not between sites. Turn on TLS in
  PostgreSQL with certificates from a small project CA, require `hostssl` for
  replication, and connect with `sslmode=verify-full`. D2 then needs nothing
  of its own: the standby's backups are made from this replication stream.
- **T2. Latency and bandwidth between sites.** *[docs]* Asynchronous replication across
  sites can lag further behind than in the lab, which widens what a failover
  can lose (C4). Measure lag over the real link, check that the RPO target of
  30 seconds holds, and that timeouts (SSH, `connect_timeout`) suit it.
- **T3. Fencing when the other site does not answer.** *[docs]* Acceptance fences the
  old primary through the Proxmox API (power off, links down, ports checked),
  which needs access to the failed site's hypervisor. With a whole site gone
  or cut off, that access may be missing, and an isolated old primary could go
  on accepting writes (split-brain). Write a procedure for how the operator
  *knows* the old site is fenced (confirmation from someone on site, power
  removed, the network closed from the surviving side), and for what to do when
  that site comes back with its old primary. The quarantine covers the return
  only when the hypervisor is reachable.
- **T4. One CA for both sites.** *[new]* The promoted host creates its own CA, so
  after a failover every user's browser shows certificate errors until the new
  CA is rolled out (acceptance trusted the new CA on its one client by hand).
  Share one CA between the sites, synchronised like the other DR secrets over
  an encrypted link (T1), or use certificates from an existing PKI.
- **T5. Pointing users at the other site.** *[docs]* Clients use `todo.test` and
  `notes.test`; acceptance edits `/etc/hosts` on one client. Write down how the
  names move to the surviving site in the real setup (a DNS change with a
  short TTL, a floating address or similar), who does it, and how long it
  takes. Without it, failover is done but nobody reaches the service.
- **T6. Planned switchover and switchback.** *[new]* Today roles change only through a
  disaster promotion: fence, promote, then rebuild the old primary with a full
  copy of every database. For maintenance at one site, switch in a controlled
  way instead: stop writes, wait for zero lag so nothing is lost, promote the
  other site, and make the old primary a standby. Switching back then costs
  another full reseed across the link between sites; `pg_rewind` can turn a
  cleanly stopped old primary into a standby without a full copy. Add commands
  for a controlled switchover and for switching back. Start with the simple
  form that reuses the existing rebuild (a full reseed); add `pg_rewind` only if
  the reseed proves too slow over the real link.

## Operator runbooks

- **O1. Short runbooks for real incidents.** *[docs]* ACCEPTANCE.md and the agent guide
  are tests of over 800 lines that describe a drill, not an incident. An
  operator without an assistant needs short pages with ready app-ops commands
  for the common cases: the primary site is gone; the standby is down or lost
  its slot; a disk is full; the certificate has expired; data was deleted by
  mistake and needs PITR. Each says how to notice it, what to check first,
  what to do and what never to do. `docs/manual-recipes/` is a starting point
  but does not cover these.

## Updates and time

- **U1. An update path for a replicated pair.** *[new]* The installer now refuses a
  host with replication, which is safer, but it leaves no supported way to roll
  out new application images or configuration after DR is set up: `install.sh`
  refuses on the primary, and `deploy-promoted-application` covers only a
  promoted host. Updates from item 14 therefore have no way into operation.
  Add an app-ops command that updates the application tier on the current
  primary while keeping the LAN publication, a fixed order for PostgreSQL
  minor updates (standby first), and a plan for major upgrades such as 17 to
  18, which cannot stream between versions.
- **U2. TLS renewal while running.** *[new]* The server certificate lasts 397 days, and
  `proxy-entrypoint.sh` issues a new one only when nginx starts with fewer than
  30 days left. nginx running for over a year without a restart serves an
  expired certificate, and nothing warns. Check the expiry in M1, and add a
  renewal that does not need a full service restart.
- **U3. Time synchronisation.** *[new]* Token expiry, TLS and log timestamps depend on
  correct clocks on both hosts. Check that chrony (or another time service) is
  active in the preflight and in acceptance phase 1, and document it.

## Security hardening

The containers already run as non-root users, with
`allowPrivilegeEscalation: false` and every capability dropped, and TLS is
limited to 1.2 and 1.3.

- **H1. Brute-force protection in Keycloak.** *[config]* `keycloak/todo-realm.json` sets
  neither `bruteForceProtected` nor a password policy, and Keycloak leaves both
  off by default, so anyone who reaches the login page can guess passwords
  without limit. Turn on temporary lockout after repeated failures and set a
  password policy in the realm import.
- **H2. HTTP security headers.** *[config]* The shared nginx sets only
  `X-Content-Type-Options`. Add HSTS, a Content-Security-Policy and
  `frame-ancestors` in `shared-proxy.yaml.j2`. The CSP must allow what the
  Keycloak adapter needs, so run the browser tests afterwards.
- **H3. Vulnerability scanning of images.** *[optional]* Dependabot (item 14) reports new
  versions, not known vulnerabilities in the packages inside the base images.
  Scan the built images in CI (for example with Trivy); a CI-only tool, never
  installed on target hosts.
- **H4. Read-only root filesystems.** *[optional]* Set `readOnlyRootFilesystem`
  where a container allows it, with writable volumes only where needed. Extra
  hardening, not a gap.

## fapolicyd

First check `grep -E '^\s*integrity' /etc/fapolicyd/fapolicyd.conf` on both
VMs (read-only). With the default `integrity = none`, fapolicyd trusts a path
whatever its current contents, which makes F2 and F4 real weaknesses.

- **F1. Correct the docs.** *[docs]* FAPOLICYD.md says trust is tied to path, size and
  hash. That only holds when `integrity` is `size`, `sha256` or `ima`; waiting
  for the exact `--dump-db` lines checks the database, not enforcement. Say
  what holds with and without an integrity check.
- **F2. Never trust user-writable files.** *[simplify]* The controller runs app_ops and
  app_installer from `~/todo-operations`, `install_trusted` trusts those
  sources on the controller, and the offline install trusts the extracted
  bundle's Python in `$HOME`. Run all trusted Python from root-owned copies
  (install to `/opt/todo/lib` first, then run from there) on the controller
  and in the offline install too, so no trust entry points into a home
  directory.
- **F3. A root-owned trust helper.** *[simplify]* `trust-files.sh` runs as text passed to
  `sh -c` from the user's operations package, so fapolicyd never checks it.
  Install it once as a root-owned file with its own trust, and run that.
- **F4. Remove stale trust.** *[new]* Replacing, renaming or deleting a file leaves its
  trust entry behind (for example the old `todo_*` tools and old bundle
  extractions); cleanup is manual today. Remove entries when files are
  replaced or retired.
- **F5. One trust file.** *[simplify]* The code and docs use `todo`, `app-installer` and
  `todo-component`. Use one name, so it is clear what the project trusts.
- **F6. State the lab limit.** *[docs]* With `NOPASSWD: ALL` the service user can do
  anything as root, so acceptance does not test fapolicyd as a barrier against
  that user. Say so in the acceptance docs.

## Firewalls

The guest firewalld rules and the Proxmox quarantine are both needed: the
first lets only the client and the peer in, the second fences an old primary.
What is weak is how they are checked and switched.

- **W2. Quarantine as one tool.** *[new]* The phase 5 rehearsal and phase 9 switch the
  Proxmox VM firewall, links and rules in many separate API calls; skipping
  one left VM 107 quarantined in run 2. Add one idempotent command that applies,
  lifts and verifies the whole profile, so lifting and checking cannot be
  forgotten halfway.
- **W3. An exact lab baseline.** *[new]* Proxmox firewall state is not part of a VM
  snapshot, and leftovers from earlier runs stay behind. Phase 1 deletes them
  by comment prefix. Add a script that resets both VMs to an exact expected
  rule list and reports anything else, and say clearly that snapshots do not
  cover this state.
- **W4. Tool-owned guest rules.** *[new]* The firewalld rules are copy-and-paste
  commands in phases 3, 4, 7 and 9, tied to fixed addresses. Let a tool add
  or at least verify them (with the app-ops zone and runtime check) before each
  DR command.
- **W5. Note the node-wide effect.** *[docs]* VM rules need the datacenter and node
  firewall on, which also changes access to the Proxmox host itself. State this
  in the agent guide's preparation part.

## After a CLEAN PASS with app-ops: retire Ansible

- **R0. Record the result first.** *[docs]* Add the run's evidence as
  `docs/history/ACCEPTANCE-<short-sha>.md`, as for earlier runs, and update the
  verdict and run list in `PROJECT.md#acceptance`, which AGENTS.md points to.
  Bring the rest of `PROJECT.md` up to date too: app-ops, the new runs and this
  backlog. Check and state whether the two standby-rebuild defects that the
  `3fb897f` record names are fixed. Move `docs/ACCEPTANCE-3fb897f.md` into
  `docs/history/` with the other evidence, changing only the links to it.
- **R0b. Replace `deploy.yml` and `uninstall.yml`.** *[docs]* They were never ported to
  app-ops because they only wrap `app_installer install` and `uninstall`. Show
  those direct commands in the guides before the playbooks go.

3. *[simplify]* Delete `deploy/ansible` and `ansible.cfg`, and take them out of the
   operations package.
4. *[simplify]* Remove the Ansible CI jobs and the tests that run playbooks.
5. *[simplify]* Rewrite `ACCEPTANCE.md`, `PROXMOX-QUARANTINE.md` and
   `ACCEPTANCE-TROUBLESHOOTING.md` for app-ops, then merge
   `ACCEPTANCE-APP-OPS.md` into `ACCEPTANCE.md`, so that one guide remains.
6. *[docs]* Update AGENTS.md: "Python installer for single-host, app-ops (plain SSH) for
   DR/multi-host", and the rule that DR installs workloads through
   `install-workload.yml`.

## Logging

Without an assistant, the logs must tell an operator what happened, where and
why. The seven workloads already log to journald (`LogDriver=journald`), and
`promotion.json` records promotions. The Python tools and backends do not log.

- **L1. Log every operations and DR command.** *[new]* No Python code uses `logging`;
  the installer, `app_dr.py`, `app_backup.py` and app-ops print to the terminal
  only, without timestamps, and keep nothing. Add one small shared helper on
  the standard library that writes to journald (for example through
  `logger -t app-ops`): timestamp, host, command, database, result and duration,
  never a secret. app-ops also keeps one log file per run on the controller.
- **L2. Keep the failure reason in the installer.** *[simplify]* `commands.run` reports only
  `podman secret failed (exit 1)` and drops stderr, so the command must be rerun
  by hand to see why. Include the stderr tail, except for commands that can
  print secrets (such as `podman secret inspect`), as app-ops and `app_dr.py`
  already do.
- **L3. Backend logging.** *[new]* The backends log almost nothing themselves, and
  `LOG_LEVEL` from `values.yaml` is set but never used (uvicorn runs at its
  default). Use it, and log rejected tokens with the reason (never the token),
  database errors with context, and changes with the user's `sub`.
- **L4. Persistent, bounded journald.** *[config]* Whether logs survive a reboot depends
  on journald storage on the hosts, which is neither set nor documented, and
  nothing bounds size or age. Configure persistent storage with limits, and
  document it.
- **L5. `docs/LOGGING.md`.** *[docs]* One page with where each log lives and ready
  commands per workload and tool, including the rootless
  `journalctl _SYSTEMD_USER_UNIT=...` form (`journalctl --user` can show nothing).
- **L6. Logs across the two hosts.** *[new]* Each VM keeps its own journal, so after
  a failover the history is split, and in a real disaster one host may be
  gone with its logs. With two machines and no third: let each host forward its
  journal to the other (for example `systemd-journal-upload`/`-remote`), so the
  surviving host also holds the lost host's logs up to the moment it was lost.
  Testable in the lab with the two VMs.

## Monitoring and backup routine

WAL on the primary is bounded (`max_slot_wal_keep_size=1GB`): a standby that is
down too long invalidates its slot, `cluster-status` reports it, and the standby
is rebuilt. What is missing is anything that tells the operator.

- **M1. Scheduled checks that alert.** *[new]* Today an operator only learns that
  replication stopped, a slot was invalidated, WAL archiving fails or a disk
  fills up by running `app_dr.py status`, `app_backup.py status` or
  `cluster-status` by hand; ARCHITECTURE.md says lag and invalidated slots need
  monitoring. Add a systemd timer that runs these checks regularly, so a failed
  check becomes a failed unit in the journal, with an optional `OnFailure=`
  mail. No new dependency.
- **M2. Scheduled backups and pruning.** *[new]* Backups are taken only when someone
  runs `app_backup.py create`, and the archive copies every WAL file into the
  backup volume while nothing removes old base backups or WAL, so the disk
  slowly fills. Add a systemd timer on both hosts, whatever their role (D2):
  a full base backup every night, and pruning that keeps the base backups of
  the last 7 days and only the WAL they need. The same timer and code run on
  the primary and on the standby.
- **M3. Regular restore tests.** *[optional]* A backup that was never restored is not
  proven. Run the existing disposable PITR restore on a schedule (for example
  weekly) and compare it with a known point, or document a manual monthly
  restore test instead.

## Data checks in acceptance

Acceptance proves replication state for all three databases (streaming, slot,
zero lag, equal LSNs), but checks content only through marker rows in todo and
notes. Keycloak's database is checked only indirectly, through a login on the
promoted primary.

- **C1. A content fingerprint per database.** *[new]* One small read-only command (in
  `app_installer`, called by app-ops) that gives, for every table, the row count
  and a hash over its rows in a fixed order. Compare primary and standby for
  all three databases after bootstrap (phase 4), just before fencing (phase 6,
  while both are reachable) and after the rebuild (phase 9).
- **C2. A direct Keycloak marker.** *[new]* For example an attribute on the test user,
  read with SQL on the standby like the todo and notes markers, including on
  the rebuilt standby in phase 9.
- **C3. PITR for Keycloak too.** *[new]* Phase 8 backs up and checks archiving for all
  three databases but restores only todo and notes. Restore Keycloak's
  database as well, and compare a known value before and after the restore
  point.
- **C4. State what asynchronous replication can lose.** *[docs]* Acceptance fences only
  after the last marker has reached the standby, so it shows failover works,
  not the worst-case loss of a crash while writing. Say plainly in
  ACCEPTANCE.md and ARCHITECTURE.md that the last transactions can be lost
  (the RPO), and that this is a design choice.

## Operations and DR decisions

- **D1. Sudo with a password in app-ops.** *[new]* app-ops needs `NOPASSWD` today, and
  acceptance grants `NOPASSWD: ALL` (see F6). As agreed when passwords were
  dropped, add password support later by running every privileged command
  through `/bin/sh`, so a narrow NOPASSWD rule can never match it and a
  password line can never become a command's stdin.
- **D2. Backups that survive losing a machine.** *[new]* (Decide D5 first.) Base backups and WAL live on
  the same VM as the database. The standby holds today's data, but not the
  history: a mistaken delete replicates within seconds, and only PITR from the
  backup undoes it, from a backup that was on the machine that was lost.
  Decided: the two machines are on separate hardware at separate sites, so
  each host backs up its own database copy, as with RMAN on an Oracle Data
  Guard standby:
  - *WAL archiving on both hosts.* The primary archives its WAL as today. The
    standby runs with `archive_mode = always` and archives the WAL it receives
    through replication, so its archive is as fresh as the primary's.
  - *A full base backup every night on both hosts* (`pg_basebackup` works
    against a standby), kept for 7 days with the WAL it needs (M2). The WAL
    archive is the incremental part: restore the last full backup from before
    the target time, then replay WAL up to it (`recovery_target_time`).
  - *No copy job and nothing to reverse.* The standby's backups come from the
    replication stream, which T1 encrypts. After a failover, both hosts go on
    as before in their new roles.
  - *Known limits.* If replication stops (for example an invalidated slot), the
    standby's archive has a gap until the rebuild, which M1 must report. After
    a rebuild, that host's history starts again from its first new base
    backup; the other host still has its own.
  - *Code.* `app_backup.py` assumes the primary today; let it configure
    archiving and take base backups on a standby too. Acceptance phase 8
    should restore from a backup taken on the standby, to prove it works.
  - *Not now.* Incremental base backups (`pg_basebackup --incremental`,
    PostgreSQL 17) add a chain that must be combined to restore; the
    databases are small, so a nightly full backup costs little. Add them only
    if a full backup one day takes too long.
  Testable in the lab with the two VMs, although they share one physical host
  there.
- **D3. `deploy-promoted-application` runs only on the promoted host.** *[docs]* It
  refuses unless that host is the machine running app-ops. Either lift the
  limit or document it as deliberate in `deploy/ops/README.md`.

- **D4. One shared database server, or one per app?** *[decision]* Today Todo,
  Notes and Keycloak each run their own PostgreSQL server, so there are three
  WAL streams, slots, archives, backups and replication ports, and at a
  failover or PITR the three are close to, but not exactly at, the same moment
  (a row in Todo or Notes can refer to a Keycloak user whose creation was lost
  in the last seconds; see C4). One shared server with three databases, as in
  a classic central database server, would give one of each and exact
  consistency, with less memory. It would cost independence: shared upgrades
  (including major versions), restarts and settings, one failure affecting
  every app and the login, PITR only for all databases at once, and a change
  to the accepted architecture (AGENTS.md: each app has its own PostgreSQL
  pod). Decide after Ansible is retired, with numbers: how much code and how
  many operating steps would go, and what would be lost. Choosing the shared
  server means a new acceptance run.

- **D5. pgBackRest instead of our own backup code?** *[decision]* Backup, WAL
  archiving and PITR are where our own code is riskiest, and a mature open
  source tool already does them: pgBackRest takes full, differential and
  incremental backups (also from a standby), archives WAL, applies retention,
  can encrypt the repository, and restores to a time or a named point. It
  would replace most of `app_backup.py` and what D2 and M2 would add, likely
  with less code in total. The cost breaks principle 2 (no new runtime
  dependencies): the official `postgres` image does not include it, so it
  needs our own image or a helper container with the data volume, it must go
  into the offline bundle, and operators must learn it. Barman and WAL-G are
  alternatives. Failover stays our own code: no ready tool fits two sites
  without a third machine together with the application tier, Keycloak and
  the quarantine (Patroni and pg_auto_failover need a quorum or monitor node,
  Pacemaker needs fencing and in practice a quorum device, CloudNativePG needs
  Kubernetes). Decide before building D2 and M2; choosing pgBackRest means a
  new acceptance run.

## DR code structure

7. **One place for paths and constants** *[simplify]* in `settings.py`: the
   `todo-kube-runtime` directory (14 places), `/opt/todo` (11), the promotion
   record path (6), the service port 8443 and the RPO of 30 seconds in app_ops.
8. **One way to run commands and SQL.** *[simplify]* `app_dr.py` and `app_backup.py` have
   their own command runners and error types, and `app_backup.py` spells out
   nine `psql` calls; use `replication.sql()` and one shared runner.
9. **Smaller units.** *[simplify]* Split `app_backup.py` (archiving, backup, restore) and
   `replication.py` (bootstrap/reseed, status checks). Give each
   `app_installer/cli.py` and `app_backup.py` command its own small function.
10. **Small cleanups.** *[simplify]* Rename `TodoDr` and `TodoBackup`; replace the manual
    `sys.path` setup in the scripts; describe the JSON contract between
    app_ops and app_installer.
11. **Backend duplication.** *[simplify]* `todo-backend` and `notes-backend` have identical
    `migrate.py` and near-identical `setup_roles.py` and `main.py`. Share them;
    this touches the image builds.

## Tests

12. **A whole-stack CI job** *[optional]* (decision needed): install the seven pods with
    real Podman and stream between two PostgreSQL instances on one runner. The
    fakes check commands and order, but only acceptance proves real behaviour
    today.
13. **Mutation testing in CI** *[optional]* runs only from the default branch
    (`schedule` and `workflow_dispatch`); it starts working after the merge.
14. **Security update routine.** *[config]* Python packages, base images (Python,
    nginx, Keycloak, PostgreSQL) and GitHub Actions are all pinned, but nothing
    reports a security fix. Enable Dependabot for pip, container images and
    Actions, so each update arrives as a pull request that CI tests. No runtime
    dependency is added.
15. *[optional]* GitHub secret scanning, or a gitleaks/trufflehog run, on top of
    the pattern search already done.

## Lab housekeeping (operator)

16. *[operator]* Rebuild the `clean-agent` snapshots with `prepare-agent-snapshots.sh`, so
    they contain `python3-jinja2` and `python3-pyyaml` (every run installs them
    as a recorded deviation today).
17. *[operator]* Remove the old `todo-lab-ca-*` nicknames from the client NSS database
    (`certutil -D -d sql:$HOME/.pki/nssdb -n NAME`).
18. *[operator]* Review and remove the old Proxmox firewall rules: three
    `todo-quarantine-*` rules and DROP policies on VM 107, and one rule without
    a comment (tcp 5432 from `.111`) on VM 108.
