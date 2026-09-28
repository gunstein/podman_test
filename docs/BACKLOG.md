# Backlog

Agreed work after the CLEAN PASS with app-ops on `2165933`
([record](history/ACCEPTANCE-2165933.md)), the baseline to compare against.
Do not change checked code while an acceptance run is in progress: the run
would then test a different revision from the one in its kickoff message.
Remove an item when its change is merged.

## Principles

The project stays simple and pedagogical (AGENTS.md). This backlog must not
turn it into a monster in code, maintenance or operation.

1. **Shrink first.** Removal and simplification come before new features.
2. **No new runtime dependencies.** Only the Python standard library, systemd,
   journald, Podman and PostgreSQL on the hosts. New tools are allowed in CI
   only.
3. **Reuse before new.** Build on what exists (app-ops commands, systemd
   timers, the DR secret synchronisation), not on new services.
4. **Less code in total.** With Ansible gone, and once the duplication is gone, the
   project should have less code than today, new features included.
5. **Optional means optional.** Drop an optional item without regret when it
   costs more than it gives.

Each item is marked: *[simplify]* removes or unifies code; *[docs]* is
documentation or a procedure; *[config]* is a setting; *[new]* adds a
feature; *[optional]* can be dropped; *[operator]* is lab work for the
operator, not code; *[decision]* needs the owner's choice before any work.

## Order

0. Acceptance tooling (A1-A4): every later item needs a trustworthy run, and
   runs 11 and 12 failed on the agent's own commands, not on the product.
   Then E1 and E2, the cheapest way to find errors before the lab does.
   After run 21: S1, the installer and DR apart in the tree, before more DR
   code is added.
   After run 22: R1-R3 from the code review of `9627adb`, so the installer
   and DR code says what it does before more is built on it.
1. Failover to Trondheim within 30 minutes (see the goal below): G1 (one
   failover command), G2 (Trondheim is ready), G3 (time it in the drill), T3
   (fencing without the Oslo hypervisor), T4 (one CA), T5 (moving the names),
   G6 (real time limits),
   M1 (alerts), O1 (incident runbooks), G4 (the disaster drill in the lab) and
   G5 (rebuilding Oslo on new hardware).
2. What operation needs: U1 (updating a replicated pair), T6 (planned
   switchover), U2 (certificate renewal, before replication stops by itself),
   M4 (a durable WAL archive), M2 (scheduled backups with pruning), L1 and L2
   (command logging, failure reasons).
3. fapolicyd (F0 first, then what is left of F1-F6), firewalls (W) and data
   checks (C).
4. DR code structure and the rest.

The real setup has two machines and no third, on separate hardware at separate
physical sites. D2 and L6 are therefore designed for two hosts that each keep
what the other would lose: each host backs up its own database copy (D2), and
each holds the other's logs (L6). Everything between them crosses a network
between sites, and replication across it uses TLS.

## Acceptance tooling

Runs 11 and 12 passed every functional gate, but both failed to be clean
because of commands the agent typed around the product: a firewall rule
without `--permanent`, a proof run before the Proxmox firewall applied, a
write probe against a column that does not exist, an exit status lost in a
pipe, a hand-copied fingerprint. The fix is fewer hand-written commands, not
more rules. `deploy/scripts/lab/acceptance.py` runs on the client only (standard
library, `pve_lab.py`, SSH, `wait-ready.sh`); it is lab tooling, never shipped
to a host. The product's own commands (`install.sh`, app-ops, `app_dr.py`,
`app_backup.py`) stay exactly as the guide writes them: they are what is
being accepted.

- **A1. Foundation and a quick run.** *[new]* A run folder with one log per
  command (time, command, output, exit status) and a `record.jsonl` line for
  each. `check` commands only read, may repeat and compare with the expected
  values themselves (PASS/FAIL); `do` commands change state and refuse to run
  again in the same run after a failure unless the operator approves. First
  commands: `check services` (via `wait-ready.sh`), `check headers`,
  `check ca`, `do reboot`, `do markers`, `do firewall-https`. With them, a
  quick acceptance (phases 1-3 with reboot and repeat install on one VM, about
  20 minutes) for changes that do not touch installer, Quadlet, replication,
  app-ops or backup. Implemented (`deploy/scripts/lab/acceptance.py`,
  [ACCEPTANCE-QUICK.md](ACCEPTANCE-QUICK.md)), plus `check clean-host`,
  `check browser`, `check markers` and `do rollback`. The first quick run
  passed with every step PASS ([record](history/QUICK-b9bffbf.md)); the item
  goes when A4 has used the tool in a full run.
- **A2. The rest of the glue.** *[new]* `check roles`, `check write-probe`
  (the guide's rolled-back inserts, verbatim), `check replication-tls`,
  `check markers` on both hosts, `check disk`, `do firewall-replication`,
  `do proxmox-firewall` (with the 20-second wait). Implemented, with
  `check roles primary|archiving|standby` and `do replication-exception`
  (finds the rule by its comment); first used in the full run of A4.
- **A3. The record from the log.** *[new]* `acceptance.py report` builds the
  draft record's tables from `record.jsonl`: IDs, fingerprints, backup names
  and every repeat, so no value is copied by hand. CLEAN PASS then means
  every step PASS and no `do` run twice. Implemented: `acceptance.py --run ID
  report` writes `REPORT.md` with every step and its values, the other logs'
  last `exit=` and `{"changed": ...}` lines, and what needs attention
  (failures, refusals, approvals, unfinished `do`, non-zero exits, a dirty
  checkout); the quick guide's verdict uses it. It also compares the run with the guide at
  the recorded revision (`report full` or `report quick`): run 15's report said
  ALL STEPS PASS although the agent had replaced two phase 6 steps. It
  compares every record under a label, not only the first: a code review
  showed that a second, different command under a correct label passed.
- **A4. A shorter agent guide.** *[simplify]* C9 becomes a command list per
  phase; the kickoff needs no special rules. One full run with the tool before
  it is trusted. ACCEPTANCE.md stays the explained guide for people. Written: C9.1-C9.11 are fixed `acceptance.py`, `product`, `vm` and `ops`
  lines (over 80 tool steps, each label once, checked by a test), with new
  tool commands for fencing, port and connection proofs, the quarantine
  profile and stop helper, links, power, `onboot` and SSH pinning. Runs 16
  and 17 were clean passes with it ([16](history/ACCEPTANCE-7402641.md),
  [17](history/ACCEPTANCE-aeefe4a.md)).

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
  Implemented as `app-ops failover` (`deploy/dr/app_ops/failover.py`), and
  acceptance phase 6 now runs it instead of `app_dr.py promote`: it prints
  the hostnames, address and CA fingerprint users need, since no DNS
  provider is chosen (T5). Run 18 accepted it
  ([record](history/ACCEPTANCE-2dbc561.md)). A code review then found that
  its final check proved the services answer, not that users can log in. A
  real login needs a person's password and a browser, which a DR tool should
  not hold, so it stays with a person and with the browser tests (acceptance
  phase 7, CI's full-stack job). `failover` now checks what it can without a
  user: services ready, and each app's login can start (Keycloak accepts the
  app's client and redirect address; the app's CSP allows the token request),
  also run against the real stack in CI. Run 19 passed it on the lab VMs
  ([record](history/ACCEPTANCE-4137c5e.md)).
- **G6. Real time limits.** *[new]* A command that hangs must not stop a
  failover silently. `commands.run` in the installer and the SSH transport of
  app-ops have no timeout; `connect_with_retry` in `migrate.py` checks its
  deadline only after a failed attempt; `_wait_for_restore_pause` promises 60
  seconds but makes 60 attempts of unbounded commands. Give each long wait one
  deadline from `time.monotonic()` and each underlying call its own limit, and
  say in the error which step ran out of time. (From the code review of
  `25ec0a6`.) Implemented: every installer command has a limit
  (`settings.COMMAND_TIMEOUT`, 10 minutes; health waits 5 minutes, images 30
  minutes, a database copy 4 hours), every app-ops command on a host 10
  minutes, and each app_installer step a larger backstop above the limits
  inside it. `connect_with_retry` gives each attempt its own
  `connect_timeout` inside the deadline, and the PITR and WAL archive waits
  in `app_backup.py` each keep one deadline. A timeout names the command and
  host; `failover` adds the step. Run 19 reached none of the limits
  ([record](history/ACCEPTANCE-4137c5e.md)).
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
- **T4. One CA for both sites.** *[decision]* The promoted host creates its own
  CA, so after a failover every client must trust a new CA before users stop
  seeing certificate errors (`failover` prints its fingerprint; acceptance
  trusts it on its one client by hand). Kept as it is for now (owner's
  decision, 2026-09-27). To decide before G3, whose timing must either include
  that trust step or not need it:
  - *Only a CA outside the nodes*, the mode `docs/TLS.md` already recommends:
    a protected issuing machine holds the CA; Oslo and Trondheim each get
    their own server certificate and private key for the same names, issued
    in advance; clients trust the root once. nginx then only reads the issued
    files. The installer checks names, chain, key match and expiry before it
    changes anything, the node key lives in a Podman secret, the standby gets
    its certificate at bootstrap (G2), and renewal starts manual with an expiry
    warning on both nodes (U2). In the lab, the client plays the issuing machine.
  - *Both modes*: `local` as today for hosts without such a CA, and
    `provided` as above. The mode is chosen at install and stored on the
    host, primary and standby must match, and `provided` never falls back to a
    local CA when a file is missing or invalid. Optionally, `local` shares one
    CA from the primary to the standby at bootstrap, like the replication CA,
    so failover keeps client trust at the cost of the CA key on both nodes.
    Full acceptance would then run `provided`; CI keeps covering `local`.

- **T5. Pointing users at the other site.** *[docs]* Decided (2026-09-27): a
  manual change in the internal DNS, which is expected to survive the loss of
  Oslo; perhaps it stays manual for good. After `failover`, the DNS owner
  points `todo.test` and `notes.test` at the address `failover` prints. The
  runbook (O1) mentions, without detail: agree on a low TTL in advance (a few
  minutes), make sure the records can be changed without Oslo (where the
  primary DNS server lives), and name who makes the change and how to reach
  them. No automatic DNS update is planned; the lab keeps `/etc/hosts` as the
  stand-in.

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
- **U2. Certificate renewal while running.** *[new]* Two certificates expire
  without anyone being warned:
  - *nginx.* The server certificate lasts 397 days, and `proxy-entrypoint.sh`
    issues a new one only when nginx starts with fewer than 30 days left. nginx
    running for over a year without a restart serves an expired certificate.
  - *Replication.* Each primary's PostgreSQL certificate lasts 825 days and is
    renewed only when the host is published as primary (bootstrap or rebuild).
    A pair that runs longer without a rebuild gets an expired certificate; the
    standby then refuses it (`verify-full`) and replication stops, which only
    `replication-status` or `cluster-status` would show. The CA lasts 10 years.

  Check the expiry of both certificates, and of the replication CA, in M1's
  scheduled checks, with a warning well before the end (for example 60 days).
  Add renewal that needs no full service restart: for replication, an app-ops
  command that runs `replication_tls.install_server_tls` on the current
  primary, which already reissues a certificate with less than 30 days left
  and reloads PostgreSQL; for nginx, a reissue followed by `nginx -s reload`.
  Run both from a systemd timer, so renewal does not depend on someone
  remembering it.
- **U3. Time synchronisation.** *[new]* Token expiry, TLS and log timestamps depend on
  correct clocks on both hosts. Check that chrony (or another time service) is
  active in the preflight and in acceptance phase 1, and document it.

## Security hardening

The containers already run as non-root users, with
`allowPrivilegeEscalation: false` and every capability dropped, and TLS is
limited to 1.2 and 1.3.

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

- **F0. Package the tools as one RPM.** *[simplify]* The usual way to run own
  code under fapolicyd is an RPM installed with `dnf`: fapolicyd trusts the
  RPM database, so the files are trusted with the right hash automatically,
  and `dnf upgrade` refreshes that. Exact-file trust with `fapolicyd-cli
  --file add` is Red Hat's documented way for a few local exceptions, which
  is how this project started (`deploy/offline/FAPOLICYD.md` already
  recommends an RPM beyond the lab). Build one `todo-tools` RPM with
  `app_installer`, `app_ops`, `app_dr.py`, `app_backup.py` and
  `app-quarantine.sh`, installed root-owned under `/opt/todo`, and ship it in
  the offline bundle; install it with `dnf install ./todo-tools-<version>.rpm`,
  never `rpm -i`, which bypasses the fapolicyd integration. That removes
  `trust-files.sh`, the trust steps in the guides and the controller's sudo
  password step, and most of F2-F5. Cost: a `.spec` file and `rpmbuild` in
  the build step (CI only, no runtime dependency), and a GPG key to sign the
  package so its origin is verified (fapolicyd trusts the RPM database
  either way). Decide whether `install.sh` on a single host uses the RPM too
  or keeps today's manual trust. A new acceptance run follows.
- **F1. Correct the docs.** *[docs]* FAPOLICYD.md says trust is tied to path, size and
  hash. That only holds when `integrity` is `size`, `sha256` or `ima`; waiting
  for the exact `--dump-db` lines checks the database, not enforcement. Say
  what holds with and without an integrity check.
- **F2. Never trust user-writable files.** *[simplify]* (Gone with F0.) The controller runs app_ops and
  app_installer from `~/todo-operations`, `install_trusted` trusts those
  sources on the controller, and the offline install trusts the extracted
  bundle's Python in `$HOME`. Run all trusted Python from root-owned copies
  (install to `/opt/todo/lib` first, then run from there) on the controller
  and in the offline install too, so no trust entry points into a home
  directory.
- **F3. A root-owned trust helper.** *[simplify]* (Gone with F0.) `trust-files.sh` runs as text passed to
  `sh -c` from the user's operations package, so fapolicyd never checks it.
  Install it once as a root-owned file with its own trust, and run that.
- **F4. Remove stale trust.** *[new]* (Gone with F0.) Replacing, renaming or deleting a file leaves its
  trust entry behind (for example the old `todo_*` tools and old bundle
  extractions); cleanup is manual today. Remove entries when files are
  replaced or retired.
- **F5. One trust file.** *[simplify]* (Gone with F0.) The code and docs use `todo`, `app-installer` and
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
  already do. Name the operation, the workload and the step that failed, not
  only the program. The backends answer a missing OIDC setting with "invalid
  token"; they should refuse to start without it, so a configuration error
  shows up in the service log and not as a client error.
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
  the primary and on the standby. Standard tools only: delete the old
  `base-*` directories, then `pg_archivecleanup` (shipped with PostgreSQL)
  removes the WAL older than the oldest kept backup.
- **M3. Regular restore tests.** *[optional]* A backup that was never restored is not
  proven. Run the existing disposable PITR restore on a schedule (for example
  weekly) and compare it with a known point, or document a manual monthly
  restore test instead.

- **M4. A durable WAL archive.** *[new]* `ARCHIVE_COMMAND` in `app_backup.py`
  copies each WAL file with `cp`, which does not fsync. PostgreSQL treats the
  file as archived as soon as the command returns and may then recycle the
  original; a power loss right after can lose the copy, which leaves a hole in
  the archive, and PITR past a hole is impossible. The PostgreSQL docs warn
  about this. Copy to a temporary name, `sync` that file, then `mv` it into
  place, keeping the existing checksum check for a file that already exists.
  Standard tools only. The code notes that the command must stay
  byte-identical on running hosts, or every run restarts them: roll the new
  command out deliberately on both hosts (the standby uses it too after D2),
  and let acceptance check the archive after a reboot as today.

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
- **D2. Backups that survive losing a machine.** *[new]* Base backups and WAL live on
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
    replication stream, which is encrypted with TLS. After a failover, both hosts go on
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
  limit or document it as deliberate in `deploy/dr/README.md`.

- **D5. pgBackRest only if the needs grow.** *[optional]* `app_backup.py` uses
  PostgreSQL's standard methods (`archive_command`, `pg_basebackup`,
  `pg_verifybackup`, `restore_command` with a recovery target), and D2, M2
  and M4 need only standard tools too. pgBackRest (or Barman, WAL-G) adds
  parallel, differential and incremental backups, compression, an encrypted
  repository and faster restores, which matter for large databases. It would
  break principle 2 (own image or helper container, offline bundle). Consider
  it only if the databases grow large, restores become too slow, or backups
  must be encrypted at rest. Failover stays our own code either way: no ready
  tool fits two sites without a third machine together with the application
  tier, Keycloak and the quarantine.

## Code review of `9627adb`

A second review, by another agent, found places where the installer and DR
code is harder to trust or read than it should be. The owner's priority: the
installer and DR code must be easy to understand and get into. Starts after
run 22 is recorded. The long CLI dispatches stay as they are: they read top
to bottom.

- **R1. The code does what its comments promise.** *[simplify]* Done, one
  commit each with a test that failed first; to be accepted with R3's run:
  1. `render()` writes a new directory next to the output and swaps it in,
     so the output holds exactly one render. Development teardown had relied
     on the stale files; the dev state file now records the YAML it played.
  2. `secrets.create_kube` refuses, before creating anything, a Kube secret
     that differs from the raw Podman secret it is made from.
  3. A failed PITR cleanup no longer hides why the restore start failed.
  4. `render.read_values()` checks `values.yaml` and names the file and the
     setting.
  5. Every `App` is built with keyword arguments, enforced by a test:
     `dataclass(kw_only=True)` needs Python 3.10, and the hosts run 3.9.
  Found on the way: `logLevel` becomes `LOG_LEVEL` in each app's ConfigMap,
  but no backend reads it. Use it or remove it.
- **R2. Honest types.** *[simplify]* Done: `REPLICATED_DATABASES` holds only
  `Database`s (each app's, then Keycloak's). `describe()` and its 22-key dict
  are gone: the DR code read six keys, all of which follow from the
  `Database`. app-ops iterates the `Database`s, the secret copy is
  `secrets.installed_names()` plus each replication password, and
  `replication-apps --details` prints what the database table in
  `docs/ACCEPTANCE.md` lists. The DR code now says `database`, not `app`,
  for a member of the group (`install_postgres(database=...)`,
  `TodoBackup(database=...)`); the `--app` options and the `applications`
  key in the DR config and promotion record stay, as files and guides use
  them.
- **R3. One visible rule for where DR finds the installer.** *[simplify]*
  Done: `deploy/dr/README.md` ("Where DR finds the installer") says, for each
  DR entry point, where it runs and where it finds `app_installer` and
  `app_dr_host`; each path line in the code points there. `app_dr.py` and
  `app_backup.py` no longer search two places: on a host they use the `lib`
  next to their `bin` (`/opt/todo/lib`), in a checkout `PYTHONPATH`. A test
  unpacks the operations package, lays it out as app-ops does on a host, and
  starts `app_ops`, `app_dr_host`, `app_dr.py` and `app_backup.py`. R1-R3
  need a full acceptance run.
- **R4. Shared backend code.** *[simplify]* Item 11 below: the largest and
  least urgent; last, possibly with its own run.
- *[optional]* Smaller points from the same review: the PostgreSQL image is
  set per app, hostnames are not validated, and there is no fixed rule for how
  much of a failed command's output an error message shows.

## DR code structure

- **S1. Separate the installer from DR in the tree.** *[simplify]* Decided
  (2026-09-28); done after run 22. A third of `app_installer` is DR only
  (`replication.py`, `replication_tls.py`, `promoted.py`, the transfer of
  replication secrets, 13 of the 18 CLI commands), and `deploy/scripts` mixes
  DR host tools, development, lab and shared scripts. The imports already go
  one way (DR uses the installer; `install.py` imports no DR module), so:
  1. Split `deploy/scripts` into shared scripts, `dev/` (dev-up, dev-down,
     run-e2e, smoke-proxy) and `lab/` (acceptance.py, pve_lab.py,
     ports-closed.sh and the other lab tools). No product change. Done:
     `deploy/scripts/README.md` lists what is where.
  2. Move DR to `deploy/dr/`: the host side as `app_dr_host` with its own CLI,
     `app_ops` and its docs, and `app_dr.py`, `app_backup.py`,
     `app-quarantine.sh` and `bootstrap-ssh-key.sh`. `app_installer` keeps
     only the single host. A test refuses any import from the installer into
     DR. Done: the offline bundle carries only `app_installer`, the operations
     package adds `deploy/dr`, app-ops stages both packages on each host, and
     the guides, CI, mutation testing and docs follow. One implementation of
     workload installation stays (AGENTS.md).
  3. A full acceptance run. Done: run 22 was a CLEAN PASS
     ([record](history/ACCEPTANCE-9627adb.md)).
  It covers much of item 9 below: `replication.py` and `cli.main()` shrink.
  While moving them, make the top comment (module docstring) of
  `replication.py` and `replication_tls.py` explain the split plainly:
  `replication.py` owns each database's role and the replication itself
  (reading state, preparing a primary, bootstrapping a standby, promotion,
  reseeding the old primary, with their safety gates), grouped as in the
  file; `replication_tls.py` only encrypts the stream (the replication CA,
  the primary's certificate for its own address, `ssl = on`) and knows
  nothing about roles. Say who calls whom (`publish_primaries` and
  `configure_primary` call `ensure_ca` and `install_server_tls`; the standby
  only uses the CA with `verify-full`), that this CA is not the nginx CA for
  users (T4), and when the certificate is renewed (U2). Done in step 2.

7. **One place for paths and constants** *[simplify]* in `settings.py`: the
   `todo-kube-runtime` directory (14 places), `/opt/todo` (11), the promotion
   record path (6), the service port 8443 and the RPO of 30 seconds in app_ops.
8. **One way to run commands and SQL.** *[simplify]* `app_dr.py` and `app_backup.py` have
   their own command runners and error types, and `app_backup.py` spells out
   nine `psql` calls; use `replication.sql()` and one shared runner.
9. **Smaller units.** *[simplify]* Split `app_backup.py` (700 lines: archiving,
   backup, restore) and `replication.py` (bootstrap/reseed, status checks).
   Give each `app_installer/cli.py` and `app_backup.py` command its own small
   function; `cli.main()` is over 200 lines and `install()` over 100.
10. **Small cleanups.** *[simplify]* Rename `TodoDr` and `TodoBackup` (they
    handle all three databases). The typed boundaries (R2) and the path
    setup (R3) are done.
11. **Backend duplication.** *[simplify]* `todo-backend` and `notes-backend` have identical
    `migrate.py` and near-identical `setup_roles.py` and `main.py`. Share them;
    this touches the image builds.

## Tests

From the test review of `5a0b544` (412 tests: installer 91 % lines and 83 %
branches, app-ops 95 % and 90 %; the fakes check commands and order, not real
SQL or Podman behaviour):

- **E1. Todo API tests with the real runtime role.** *[new]* The Todo API tests
  in CI connect as the migration role, which has more rights than `todo_app`
  has in production; a missing `GRANT` would only show in the lab. Run CRUD
  through the API as `todo_app`, as the Notes tests already do. Implemented:
  `todo-backend/role_tests` runs in CI as `todo_app` (API CRUD; DDL,
  `TRUNCATE` and `schema_migrations` denied) and fails when a grant is
  revoked. It replaces the inline privilege probe in the workflow.
- **E2. A small full-stack job in CI.** *[new]* Before merging: `dev-up.sh` on
  the runner's rootless Podman, a real Keycloak login and one write and read
  in each app through nginx, then `dev-down.sh`. Run 10's CSP error was found
  only on the lab VMs; this job would have caught it in minutes. It narrows
  item 12 below to the part that needs no second host. Implemented: the
  `full-stack` job runs on `ubuntu-26.04` (Podman 5.7; `ubuntu-latest` has
  4.9, without `--no-pod-prefix`) and runs the lab's Todo, Notes and
  multi-app SSO browser tests, about ten minutes. Its first run found a real
  defect: Podman 5.7 lets `envFrom` win over `env`, so the migration
  container ran as `todo_app`. `DATABASE_USER` now lives only in `env`, and
  a test refuses a variable set in both places. Run 17 accepted the change
  ([record](history/ACCEPTANCE-aeefe4a.md)).
- **E3. Installer CLI branches.** *[new]* The installer's `cli.py` has 46 %
  branch coverage. Test its commands and failure paths through the CLI, done
  together with splitting `cli.main()` (DR code structure, item 9).
- **E4. A coverage report in CI.** *[config]* Print line and branch coverage
  for the Python suites on every run, as information to follow up important
  missing branches, not as a percentage gate.
- **E5. Browser tests for failure.** *[optional]* An expired session and a real
  token refresh against Keycloak, and what the user sees when the backend or
  Keycloak is down. Data surviving a page reload and a service restart is
  already covered by the acceptance markers.

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

## Documentation to consider

- **Align overview and runtime guides with the accepted seven-pod design.** *[docs] [optional]*
  Review `README.md` and `deploy/runtime/README.md` against `AGENTS.md`,
  `docs/ARCHITECTURE.md` and the current installer. Some passages still describe
  six workload units or put Keycloak in Todo's database, and the README's
  two-node section says Notes replication and backup are future work. Check the
  actual implementation and acceptance record before correcting those passages;
  keep this as a documentation review, not a runtime change.

## Lab housekeeping (operator)

16. *[operator]* Rebuild the `clean-agent` snapshots with `prepare-agent-snapshots.sh`, so
    they contain `python3-jinja2` and `python3-pyyaml` (every run installs them
    as a recorded deviation today).
17. *[operator]* Remove the old `todo-lab-ca-*` nicknames from the client NSS database
    (`certutil -D -d sql:$HOME/.pki/nssdb -n NAME`).
18. *[operator]* Review and remove the old Proxmox firewall rules: three
    `todo-quarantine-*` rules and DROP policies on VM 107, and one rule without
    a comment (tcp 5432 from `.111`) on VM 108.
