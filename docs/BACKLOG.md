# Backlog

Agreed work that is not done yet. The baseline to compare against is the
CLEAN PASS on `89b369e` ([record](history/ACCEPTANCE-89b369e.md)). Do not
change checked code while an acceptance run is in progress: the run would then
test a different revision from the one in its kickoff message. Remove an item
when its change has passed acceptance; the acceptance records and Git keep
what was done.

## Principles

The project stays simple and pedagogical (AGENTS.md). This backlog must not
turn it into a monster in code, maintenance or operation.

1. **Shrink first.** Removal and simplification come before new features.
2. **No new runtime dependencies.** Only the Python standard library, systemd,
   journald, Podman and PostgreSQL on the hosts. New tools are allowed in CI
   only.
3. **Reuse before new.** Build on what exists (app-ops commands, systemd
   timers, the DR secret synchronisation), not on new services.
4. **Less code in total.** The project should end up with less code than
   today, new features included. The two example apps stay separate on
   purpose (see Code structure): that is not duplication to remove.
5. **Optional means optional.** Drop an optional item without regret when it
   costs more than it gives.

Each item is marked: *[simplify]* removes or unifies code; *[docs]* is
documentation or a procedure; *[config]* is a setting; *[new]* adds a
feature; *[optional]* can be dropped; *[operator]* is lab work for the
operator, not code; *[decision]* needs the owner's choice before any work.

## Order

1. First, so a failover does not lose weeks of data: T3 (fencing without the
   Oslo hypervisor, a procedure). When to
   start is the owner's call.
2. The rest of failover to Trondheim within 30 minutes (see the goal below):
   G3 (time it in the drill; with T4 kept as it is, the time includes the
   client trust step), G4 (the disaster drill in the
   lab) and G5 (rebuilding Oslo on new hardware).
3. What operation needs: U1 (updating a replicated pair), T6 (planned
   switchover), U2 (certificate renewal, before replication stops by itself)
   and L1 (command logging).
4. fapolicyd (F0 first), firewalls (W) and data checks (C).
5. The rest.

For a single host without DR (`install.sh` only), what matters, in order:
the nginx part of U2 (the certificate
expires after 397 days without a restart), and Q3 (security updates, which `install.sh` can roll out). Then U3
and L4. F0, S4, E3, E8 and O2 do not change how a single host runs.

The real setup has two machines and no third, on separate hardware at separate
physical sites. D2 and L6 are therefore designed for two hosts that each keep
what the other would lose: each host backs up its own database copy (D2), and
each holds the other's logs (L6). Everything between them crosses a network
between sites, and replication across it uses TLS.

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
("Oslo is lost", T3), after which one command, `app-ops failover`, does the
rest (accepted in runs 18-22).

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
  - *A name change like DNS.* `/etc/hosts` on the client as the documented
    stand-in for the manual DNS change (runbooks/primary-lost.md).
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

- **T2. Latency and bandwidth between sites.** *[docs]* Asynchronous
  replication across sites can lag further behind than in the lab, which
  widens what a failover can lose (C4). Measure lag over the real link, check
  that the RPO target of 30 seconds holds, and that timeouts (SSH,
  `connect_timeout`) suit it.
- **T3. Fencing when the other site does not answer.** *[docs]* Acceptance
  fences the old primary through the Proxmox API (power off, links down,
  ports checked), which needs access to the failed site's hypervisor. With a
  whole site gone or cut off, that access may be missing, and an isolated old
  primary could go on accepting writes (split-brain). Write a procedure for
  how the operator *knows* the old site is fenced (confirmation from someone
  on site, power removed, the network closed from the surviving side), and
  for what to do when that site comes back with its old primary. The
  quarantine covers the return only when the hypervisor is reachable.
- **T4. One CA for both sites.** *[decision]* The promoted host creates its own
  CA, so after a failover every client must trust a new CA before users stop
  seeing certificate errors (`failover` prints its fingerprint; acceptance
  trusts it on its one client by hand). Kept as it is for now (owner's
  decision, 2026-09-27, confirmed 2026-09-29: no offline CA administration yet,
  perhaps later). Until then G3's timing includes that trust step. The client
  CA and the replication CA stay separate in every option. The options, for
  when it is taken up again:
  - *Only a CA outside the nodes*, the mode `docs/TLS.md` already recommends:
    a protected issuing machine holds the CA; Oslo and Trondheim each get
    their own server certificate and private key for the same names, issued
    in advance; clients trust the root once. nginx then only reads the issued
    files. The installer checks names, chain, key match and expiry before it
    changes anything, the node key lives in a Podman secret, the standby gets
    its certificate at bootstrap (the DR check then requires it), and renewal starts manual with an expiry
    warning on both nodes (U2). In the lab, the client plays the issuing machine.
  - *Both modes*: `local` as today for hosts without such a CA, and
    `provided` as above. The mode is chosen at install and stored on the
    host, primary and standby must match, and `provided` never falls back to a
    local CA when a file is missing or invalid. Optionally, `local` shares one
    CA from the primary to the standby at bootstrap, like the replication CA,
    so failover keeps client trust at the cost of the CA key on both nodes.
    Full acceptance would then run `provided`; CI keeps covering `local`.
  - *Preferred when it is taken up (2026-09-29):* `provided`, with the CA
    offline rather than on a server: an encrypted USB stick or folder on an
    administrator's machine, used about once a year to issue both hosts'
    certificates, with a backup copy and an expiry reminder (U2). No third
    machine runs anything; the code work is that of `provided`, in three
    steps: one host with `provided` while dev and CI keep `local`, then DR
    (the standby's certificate at bootstrap, `preflight-standby` and
    `failover` refuse a missing or invalid one, `failover` no longer asks for
    client trust), then the acceptance guide (the client issues the root and
    both certificates before phase 1; phase 7 loses its trust step).
  - *If a CA on the hosts is ever chosen instead:* limit it with
    `nameConstraints` to the registered app names, keep its key in Podman
    secrets on the two hosts only, and plan how to replace it if a host is
    compromised.
- **T6. Planned switchover and switchback.** *[new]* Today roles change only
  through a disaster promotion: fence, promote, then rebuild the old primary
  with a full copy of every database. For maintenance at one site, switch in a
  controlled way instead: stop writes, wait for zero lag so nothing is lost,
  promote the other site, and make the old primary a standby. Start with the
  simple form that reuses the existing rebuild (a full reseed); add
  `pg_rewind` only if the reseed proves too slow over the real link.

## Documentation

- **O2. Align the runtime guide with the seven pods.** *[docs]*
  `deploy/runtime/README.md` still says "six" workloads and units in several
  places, and `README.md` may have similar passages. Check them against
  `AGENTS.md`, `docs/ARCHITECTURE.md` and the installer; documentation only.

## Updates and time

- **U1. An update path for a replicated pair.** *[new]* The installer refuses a
  host with replication, which is safer, but it leaves no supported way to
  roll out new application images or configuration after DR is set up:
  `install.sh` refuses on the primary, and `deploy-promoted-application`
  covers only a promoted host. Updates from Q3 therefore have no way into
  operation. Add an app-ops command that updates the application tier on the
  current primary while keeping the LAN publication, a fixed order for
  PostgreSQL minor updates (standby first), and a plan for major upgrades such
  as 17 to 18, which cannot stream between versions.
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

  Check the expiry of both certificates, and of the replication CA, in the
  scheduled DR check (`app_dr.py check`), with a warning well before the end (for example 60 days).
  Add renewal that needs no full service restart: for replication, an app-ops
  command that runs `replication_tls.install_server_tls` on the current
  primary, which already reissues a certificate with less than 30 days left
  and reloads PostgreSQL; for nginx, a reissue followed by `nginx -s reload`.
  Run both from a systemd timer, so renewal does not depend on someone
  remembering it.
- **U3. Time synchronisation.** *[new]* Token expiry, TLS and log timestamps
  depend on correct clocks on both hosts. Check that chrony (or another time
  service) is active in the preflight and in acceptance phase 1, and document
  it.

## Monitoring and backup routine

WAL on the primary is bounded (`max_slot_wal_keep_size=1GB`): a standby that is
down too long invalidates its slot, `cluster-status` reports it, and the standby
is rebuilt. On both DR hosts `todo-dr-check.timer` reports that, failed WAL
archiving and a filling disk as a failed unit every 15 minutes, and
`todo-backup.timer` takes and prunes the primary's base backups every night
(accepted in run 32; deploy/dr/README.md). A single host gets the same nightly
backup from `install.sh` and restores to last night, and the WAL archive
survives a power loss (both accepted in run 35). `app_backup.py --app A
restore --target-time T` restores to a time from the newest base backup
before it (accepted in run 40).

- **M3. Regular restore tests.** *[optional]* A backup that was never restored
  is not proven. Run the existing disposable PITR restore on a schedule (for
  example weekly) and compare it with a known point, or document a manual
  monthly restore test instead.

## Logging

Without an assistant, the logs must tell an operator what happened, where and
why. The seven workloads already log to journald (`LogDriver=journald`), and
`promotion.json` records promotions. The Python tools and backends do not log.

- **L1. Log every operations and DR command.** *[new]* No Python code uses
  `logging`; the installer, `app_dr.py`, `app_backup.py` and app-ops print to
  the terminal only, without timestamps, and keep nothing. Add one small
  shared helper on the standard library that writes to journald (for example
  through `logger -t app-ops`): timestamp, host, command, database, result and
  duration, never a secret. app-ops also keeps one log file per run on the
  controller.
- **L3. Backend logging.** *[new]* *[optional]* The example apps, not the
  core of the repository. The backends log almost nothing, and `logLevel`
  in `values.yaml` becomes `LOG_LEVEL` in each app's ConfigMap, but no
  backend reads it. Use it (or remove it), and log rejected tokens with the
  reason (never the token), database errors with context, and changes with
  the user's `sub`. A backend should also refuse to start without its OIDC
  settings, instead of answering every request with "invalid token".
- **L4. Persistent, bounded journald.** *[config]* Whether logs survive a
  reboot depends on journald storage on the hosts, which is neither set nor
  documented, and nothing bounds size or age. Configure persistent storage
  with limits, and document it.
- **L5. `docs/LOGGING.md`.** *[docs]* One page with where each log lives and
  ready commands per workload and tool, including the rootless
  `journalctl _SYSTEMD_USER_UNIT=...` form (`journalctl --user` can show
  nothing).
- **L6. Logs across the two hosts.** *[new]* Each VM keeps its own journal, so
  after a failover the history is split, and in a real disaster one host may
  be gone with its logs. Let each host forward its journal to the other (for
  example `systemd-journal-upload`/`-remote`), so the surviving host also
  holds the lost host's logs up to the moment it was lost. Testable in the
  lab with the two VMs.

## Data checks in acceptance

Acceptance proves replication state for all three databases (streaming, slot,
zero lag, equal LSNs), but checks content only through marker rows in todo and
notes. Keycloak's database is checked only indirectly, through a login on the
promoted primary.

- **C1. A content fingerprint per database.** *[new]* One small read-only
  command in `app_dr_host`, called by app-ops, that gives, for every table,
  the row count and a hash over its rows in a fixed order. Compare primary and
  standby for all three databases after bootstrap (phase 4), just before
  fencing (phase 6, while both are reachable) and after the rebuild (phase 9).
- **C2. A direct Keycloak marker.** *[new]* For example an attribute on the
  test user, read with SQL on the standby like the todo and notes markers,
  including on the rebuilt standby in phase 9.
- **C3. PITR for Keycloak too.** *[new]* Phase 8 backs up and checks archiving
  for all three databases but restores only todo and notes. Restore
  Keycloak's database as well, and compare a known value before and after the
  restore point.
- **C4. State what asynchronous replication can lose.** *[docs]* Acceptance
  fences only after the last marker has reached the standby, so it shows
  failover works, not the worst-case loss of a crash while writing. Say
  plainly in ACCEPTANCE.md and ARCHITECTURE.md that the last transactions can
  be lost (the RPO), and that this is a design choice.

## Operations and DR decisions

- **D1. Sudo with a password in app-ops.** *[new]* app-ops needs `NOPASSWD`
  today, and acceptance grants `NOPASSWD: ALL` (see F6). Add password support
  by running every privileged command through `/bin/sh`, so a narrow NOPASSWD
  rule can never match it and a password line can never become a command's
  stdin.
- **D2. Backups that survive losing a machine.** *[new]* Base backups and WAL
  live on the same VM as the database. The standby holds today's data, but not
  the history: a mistaken delete replicates within seconds, and only PITR from
  the backup undoes it, from a backup that was on the machine that was lost.
  Decided: each host backs up its own database copy, as with RMAN on an Oracle
  Data Guard standby:
  - *WAL archiving on both hosts.* The primary archives its WAL as today. The
    standby runs with `archive_mode = always` and archives the WAL it receives
    through replication, so its archive is as fresh as the primary's.
  - *A full base backup every night on both hosts* (`pg_basebackup` works
    against a standby), kept for 7 days with the WAL it needs, as
    `app_backup.py nightly` already does on the primary.
  - *No copy job and nothing to reverse.* After a failover, both hosts go on
    as before in their new roles.
  - *Known limits.* If replication stops (for example an invalidated slot), the
    standby's archive has a gap until the rebuild, which the DR check
    reports (the standby no longer receives WAL). After
    a rebuild, that host's history starts again from its first new base
    backup; the other host still has its own.
  - *Code.* `app_backup.py` assumes the primary today; let it configure
    archiving and take base backups on a standby too. Acceptance phase 8
    should restore from a backup taken on the standby.
  - *Not now.* Incremental base backups (`pg_basebackup --incremental`) add a
    chain that must be combined to restore; add them only if a full backup
    one day takes too long.
- **D7. Shrink the packages after D6.** *[simplify]* Nothing installs
  `generated/kube-runtime` or the `deploy/quadlet/*.kube.j2` templates from a
  package any more; drop them from both packages and from the package tests,
  which then compare `generated/target` instead.
- **D8. A hostname in acceptance.** *[decision]* A public hostname other than
  the default is covered by unit tests only. Decide whether acceptance gets a
  step that installs the primary with one and checks it after failover.
- **D3. Say that `deploy-promoted-application` runs on the promoted host.**
  *[docs]* It refuses unless that host is the machine running app-ops
  (`local: true`). `failover` runs there anyway, so document the limit as
  deliberate in `deploy/dr/README.md` and remove the item.
- **D10. Re-seed a standby that lost its slot, without a failover.** *[new]*
  `rebuild-standby` expects the old primary after a failover: it requires
  every application unit loaded and stopped, which a database-only standby
  does not have, and it refuses when the rebuild slot already exists, so a
  pair can be rebuilt only once. Today the
  [runbook](runbooks/standby-rebuild.md) removes the standby's volumes and
  the lost slots by hand and runs `bootstrap-standby` again (untested). Make
  that one guarded command, and test it in acceptance.
- **D5. pgBackRest only if the needs grow.** *[optional]* Decided 2026-10-04:
  keep the own tools, which only orchestrate PostgreSQL's standard methods
  ([architecture](ARCHITECTURE.md#own-scripts-not-a-backup-or-ha-product));
  D2 needs only standard tools too. pgBackRest (or Barman, WAL-G) adds
  parallel and incremental backups, compression, an encrypted repository and
  faster restores, which matter for large databases, but breaks principle 2;
  Patroni or repmgr add automatic failover, which this design leaves to a
  person. Reconsider only if the databases grow large, restores become too
  slow, backups must be encrypted or kept off the host as a repository, or
  more standbys or automatic failover are wanted.

## fapolicyd

First check `grep -E '^\s*integrity' /etc/fapolicyd/fapolicyd.conf` on both
VMs (read-only). With the default `integrity = none`, fapolicyd trusts a path
whatever its current contents.

- **F0. Package the tools as one RPM.** *[simplify]* The usual way to run own
  code under fapolicyd is an RPM installed with `dnf`: fapolicyd trusts the
  RPM database, so the files are trusted with the right hash automatically,
  and `dnf upgrade` refreshes that. Exact-file trust with `fapolicyd-cli
  --file add` is Red Hat's documented way for a few local exceptions, which is
  how this project started. Build one `todo-tools` RPM with `app_installer`,
  `app_dr_host`, `app_ops`, `app_dr.py`, `app_backup.py` and
  `app-quarantine.sh`, installed root-owned under `/opt/todo`, and ship it in
  the offline bundle; install it with `dnf install ./todo-tools-<version>.rpm`,
  never `rpm -i`. That removes `trust-files.sh`, the trust steps in the guides
  and the controller's sudo password step, and with them today's weaknesses:
  trusted Python in user-writable home directories (the operations package,
  the extracted bundle), `trust-files.sh` run as text through `sh -c` so
  fapolicyd never checks it, stale trust entries left behind when files are
  replaced or renamed, and three trust file names (`todo`, `app-installer`,
  `todo-component`). Cost: a `.spec` file and `rpmbuild` in the build step (CI
  only), and a GPG key to sign the package. Decide whether `install.sh` on a
  single host uses the RPM too. A new acceptance run follows.
- **F1. Correct the docs.** *[docs]* FAPOLICYD.md says trust is tied to path,
  size and hash. That only holds when `integrity` is `size`, `sha256` or
  `ima`; waiting for the exact `--dump-db` lines checks the database, not
  enforcement. Say what holds with and without an integrity check.
- **F6. State the lab limit.** *[docs]* With `NOPASSWD: ALL` the service user
  can do anything as root, so acceptance does not test fapolicyd as a barrier
  against that user. Say so in the acceptance docs.

## Firewalls

The guest firewalld rules and the Proxmox quarantine are both needed: the
first lets only the client and the peer in, the second fences an old primary.
What is weak is how they are checked and switched.

- **W2. Quarantine as one tool.** *[decision]* The phase 5 rehearsal and phase
  9 switch the Proxmox VM firewall, links and rules in many separate steps.
  `acceptance.py` now has `do quarantine-profile` and `do quarantine-stop` for
  the lab. A product command that applies, lifts and verifies the whole
  profile only makes sense if the real sites run Proxmox; decide that first.
- **W3. An exact lab baseline.** *[new]* Proxmox firewall state is not part of
  a VM snapshot, and leftovers from earlier runs stay behind. Add a check that
  compares both VMs with an exact expected rule list and reports anything
  else, and say clearly that snapshots do not cover this state.
- **W4. Tool-owned guest rules.** *[new]* The firewalld rules are added by
  fixed commands in phases 3, 4, 7 and 9, tied to fixed addresses. Let a tool
  add or at least verify them (with the app-ops zone and runtime check) before
  each DR command.
- **W5. Note the node-wide effect.** *[docs]* VM rules need the datacenter and
  node firewall on, which also changes access to the Proxmox host itself.
  State this in the agent guide's preparation part.

## Code structure

The owner's priority: the installer and DR code must be easy to understand
and get into. The long CLI dispatches stay as they are: they read top to
bottom.

The todo and notes backends are deliberately separate examples. The
repository is about Podman, the installer and DR; the apps are there so the
installer and DR have two independent apps, each with its own database,
roles, secrets and images, to install, replicate and fail over. Sharing code
between them is not a goal: each app should read on its own.

- **S4. Split only along a real contract.** *[simplify]* Not by line
  count: after S3, `app_backup.py` is 108 lines shorter, and its parts
  (archiving, base backup, restore point, disposable PITR) share one
  database, volume, image and set of invariants; `replication.py` follows
  one lifecycle (inspect, prepare a primary, bootstrap a standby, promote,
  reseed). Splitting them into manager classes would make one operation
  harder to follow. Split a part out only when it has a public contract of
  its own and can be tested without the rest of the lifecycle. The one
  candidate today is `install.install()` (118 lines), into named steps, if
  that reads better.
## Tests and CI

The fakes check commands and order, not real SQL or Podman behaviour; CI's
full-stack job and acceptance cover that.

- **E3. Installer CLI branches.** *[new]* Measure the branch coverage of
  `app_installer/cli.py` and `app_dr_host/cli.py` again (the last figure, 46
  %, is from before S1), and test the commands and failure paths that are
  missing through the CLI.
- **E4. A coverage report in CI.** *[config]* Print line and branch coverage
  for the Python suites on every run, as information to follow up important
  missing branches, not as a percentage gate.
- **E5. Browser tests for failure.** *[optional]* An expired session and a real
  token refresh against Keycloak, and what the user sees when the backend or
  Keycloak is down.
- **E8. Error paths in `images.py`.** *[new]* A wrong proxy label, offline
  with `refresh_images` refused, and a missing bundle.
- **Q1. Replication in CI.** *[optional]* *[decision]* Stream between two
  PostgreSQL instances on one runner, so replication is tested before the lab.
  The full-stack job already covers the single host.
- **Q2. Mutation testing in CI.** *[optional]* It runs only from the default
  branch (`schedule` and `workflow_dispatch`); it starts working after the
  merge.
- **Q3. Security update routine.** *[config]* Python packages, base images
  (Python, nginx, Keycloak, PostgreSQL) and GitHub Actions are all pinned, but
  nothing reports a security fix. Enable Dependabot for pip, container images
  and Actions, so each update arrives as a pull request that CI tests.
- **Q4. Secret scanning.** *[optional]* GitHub secret scanning, or a
  gitleaks/trufflehog run, on top of the pattern search already done.

## Security hardening

The containers already run as non-root users, with
`allowPrivilegeEscalation: false` and every capability dropped, and TLS is
limited to 1.2 and 1.3.

- **H3. Vulnerability scanning of images.** *[optional]* Dependabot (Q3)
  reports new versions, not known vulnerabilities in the packages inside the
  base images. Scan the built images in CI (for example with Trivy).
- **H4. Read-only root filesystems.** *[optional]* Set
  `readOnlyRootFilesystem` where a container allows it, with writable volumes
  only where needed.

## Lab platform

- **P1. The lab on libvirt/KVM instead of Proxmox.** *[new]* *[optional]*
  Only if Proxmox has to go. The product does not depend on the hypervisor;
  the lab tools and the quarantine guides do. Every operation acceptance uses
  has a `virsh` counterpart, run over SSH with no new dependency:
  snapshot rollback (`snapshot-revert`, internal qcow2 snapshots), power
  (`start`/`shutdown`/`reboot`/`destroy`, polling `domstate`), links
  (`domif-setlink ... down/up`), `onboot` (`autostart --disable`) and Guest
  Agent exec (`qemu-agent-command` with `guest-exec`; the same QEMU agent, so
  `install-quarantine-tool` stays as it is). Three things take real work: the
  quarantine firewall becomes nwfilter or host nftables rules instead of
  Proxmox VM rules (toggling the replication exception means swapping a
  filter reference); libvirt access is root-equivalent on the host unless
  polkit rules narrow it per VM and action, where the Proxmox token is scoped
  today; and the fencing check "not managed by HA" falls away (no HA layer).
  One gain: the filter reference lives in the domain XML, which the snapshot
  holds, so W3 goes away. Work: a libvirt driver in place of `pve_lab.py`,
  the Proxmox paths in `acceptance.py`, phases 1, 5, 6 and 9 and
  PROXMOX-QUARANTINE.md.

## Lab housekeeping (operator)

- **K1.** *[optional]* *[operator]* Rebuild the `clean-agent` snapshots with
  `prepare-agent-snapshots.sh`, so they hold `python3-pyyaml` and no longer
  the unused `python3-jinja2` an older version of the script installed.
- **K2.** *[operator]* Remove the old `todo-lab-ca-*` nicknames from the
  client NSS database (`certutil -D -d sql:$HOME/.pki/nssdb -n NAME`).
- **K3.** *[operator]* Review and remove the old Proxmox firewall rules: three
  `todo-quarantine-*` rules and DROP policies on VM 107, and one rule without
  a comment (tcp 5432 from `.111`) on VM 108.
