# Backlog

Agreed work that is not done yet. The baseline to compare against is the
CLEAN PASS on `8ef9e83` ([record](history/ACCEPTANCE-8ef9e83.md)). Do not
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

1. Done in code, and in the acceptance guide since 2026-10-10 (steps
   `03-1u1` to `03-1u5`, `01-0-readiness-refused`, and the CI check after
   `dev-down.sh`): V1, V2 and P2, to pass the next run. Owner's steps: Q3 (Dependabot on the default branch), L4 (the
   journal in the clean snapshots, K1) and a review of T3's procedure.
2. The rest of failover to Trondheim within 30 minutes (see the goal below):
   G4 (the disaster drill in the lab, which also tests T3) and G5
   (rebuilding Oslo on new hardware).
3. What operation needs: U1 (updating a replicated pair), T6 (planned
   switchover) and U2 (the replication CA and nginx; the replication
   certificate renews itself now).
4. D2 (backups that survive losing a machine), decided and not started.
5. fapolicyd (F0 first), firewalls (W) and data checks (C).
6. The rest.

For a single host without DR (`install.sh` only), what matters, in order:
Q3 (security updates, which `install.sh` can roll out), then L4 on the
host. F0 does not change how a single host runs.

The real setup has two machines and no third, on separate hardware at separate
physical sites. D2 and L6 are therefore designed for two hosts that each keep
what the other would lose: each host backs up its own database copy (D2), and
each holds the other's logs (L6). Everything between them crosses a network
between sites, and replication across it uses TLS.

## Secrets and leftovers on disk

- **V1. Kube secret volumes that outlive their secrets.** *[done; in acceptance
  from the next run]* Found 2026-10-09 with Podman 4.9 while nginx's TLS files moved
  to Podman secrets: `podman kube play` writes the files of every `secret:`
  volume, passwords included, into a named volume called after the Kube
  secret, rewrites it at every play, and keeps it after `kube down` and
  after the secret is removed. Done 2026-10-09: `secrets.remove_kube_volumes()`
  removes each Kube secret's volume no container uses; `uninstall` always
  calls it (a copy, not data) and so does the development `down`
  (`dev-down.sh`). A rotation needs nothing more: the next play rewrites the
  files (checked with Podman 4.9). docs/SECRETS.md says where the copies
  live. Still to see on the lab's Podman 5.8: `podman volume ls` lists the
  `*-kube-*-secret` volumes while the stack runs and none after `uninstall`.

- **V2. One uninstall that leaves nothing behind.** *[done; in acceptance
  from the next run]* Found 2026-10-09 on the acceptance client: an old
  per-container install from `quadlet-reference-v1` started at every login
  and held port 8080, and `uninstall --remove-data` removed only part of it.
  Done 2026-10-09, from the lists of the retired playbook
  `ansible/uninstall.yml`: `uninstall` also removes that old install (its
  `.container` files, `todo.network`, the network `todo-network`, the
  container `todo-keycloak`, `localhost/todo-keycloak:m12` and their
  services) and names what it removed; `install.preflight` keeps refusing a
  host with it. `--remove-backups` (only with `--remove-data`, never on a DR
  host) also removes the backup volumes and says that nothing can be
  restored. `uninstall` also removes `shared-nginx-config`, the volume
  `podman kube play` makes of nginx's ConfigMap. Still to see on the
  acceptance client or a lab VM: after `uninstall --remove-data
  --remove-backups`, `podman ps -a`, `podman volume ls` and `podman secret
  ls` show nothing of the project, apart from the PostgreSQL image.

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
rest (accepted in runs 18-22). Acceptance times it: users logged in on the
promoted host 5 min 18 s after the fence in run 45, and `report full` needs
attention above 30 minutes (G3).

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
- **T3. Fencing when the other site does not answer.** *[docs]* *[done 2026-10-09;
  owner: review the three accepted proofs]* docs/runbooks/fence-without-oslo.md. Acceptance
  fences the old primary through the Proxmox API (power off, links down,
  ports checked), which needs access to the failed site's hypervisor. With a
  whole site gone or cut off, that access may be missing, and an isolated old
  primary could go on accepting writes (split-brain). Write a procedure for
  how the operator *knows* the old site is fenced (confirmation from someone
  on site, power removed, the network closed from the surviving side), and
  for what to do when that site comes back with its old primary. The
  quarantine covers the return only when the hypervisor is reachable.
- **T4. One CA for both sites.** *[step 1 of 3 done]* Step 1, provided mode on a
  single host, is done: `deploy/scripts/app_ca.py` (an offline CA with name
  constraints), `app_installer tls-request` (key made on the host,
  only the CSR leaves), `tls-install` (every check before any change, no
  fallback to the demo CA) and the nightly expiry check (docs/TLS.md). Since
  2026-10-09 the key and certificates are Podman secrets by owner's decision,
  as the worked example of files as Podman secrets (docs/TLS.md); the TLS
  volume stays, commented out, for going back, at the price of a restart
  instead of a reload when a certificate changes. Step 2, DR, is done too: `app-ops nginx-tls-request`
  and `nginx-tls-install` give both hosts their certificate (the standby its
  TLS volume and proxy image first), the pair's mode is recorded on both,
  `app_dr.py check` requires a fitting certificate, `renew-tls` prepares
  the next requests, and `deploy-promoted-application` refuses to start
  nginx without one, so `failover` needs no client trust step
  (`client_trust: unchanged`). Step 3, acceptance, remains: the guide does
  not run provided mode yet (tests/test_acceptance_guide.py lists the two
  commands as not yet in it). Since 2026-10-08 the CA may run on the same
  host as Podman, from its own storage (docs/TLS.md, "Two security
  levels"); an administrator machine is no longer required. What is left
  before v1 is T7.
  The promoted host creates its own
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
- **T7. The CA under root before v1.** *[new]* Provided mode runs with
  the CA on the same host (`app_ca.py`), owned by the Podman user in
  development. Before v1:
  - Move it to root: `/var/lib/platform-ca` root 0700, and the Podman user may
    run only `sudo platform-ca-sign` (CSR in, certificate out, no arguments).
    The wrapper and `app_ca.py sign-stdin` exist and are tested; the
    installation (root-owned copies, sudoers line, `init` as root) is the
    manual list in docs/TLS.md. Make it one reviewed installer step, and
    check the sudoers rule in the preflight.
  - Decide where the passphrase lives: typed at each signing (strongest,
    no unattended signing), or `/etc/platform-ca/passphrase` root 0400 (then the
    encryption protects only copies and backups of the CA directory).
  - A DR pair: decide which host holds the CA and how it is backed up. A CA
    only on the primary is lost with Oslo; the standby's next renewal then
    needs a new CA, and every client must trust it.
  - Full root compromise of the host is CA compromise; that stays the
    accepted limit of a same-host CA (docs/TLS.md). A CA on a separate
    machine or an organisational PKI removes it without code changes.
  - The switch from the pending to the active pair is a sequence of
    renames, not one atomic step. nginx reads the pair only at start and at
    the reload that follows the switch, and a start in between fails closed
    and is restarted; a single symlink swap would close even that window.
- **T6. Planned switchover and switchback.** *[new]* Today roles change only
  through a disaster promotion: fence, promote, then rebuild the old primary
  with a full copy of every database. For maintenance at one site, switch in a
  controlled way instead: stop writes, wait for zero lag so nothing is lost,
  promote the other site, and make the old primary a standby. Start with the
  simple form that reuses the existing rebuild (a full reseed); add
  `pg_rewind` only if the reseed proves too slow over the real link.

## Documentation

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
- **U2. Certificate renewal while running.** *[partly done]*
  - *Replication server certificate: done.* `platform-replication-tls.timer`
    runs `app_dr.py renew-tls` every night on both hosts; on the primary it
    issues a new certificate for the same address once fewer than 30 days
    are left and reloads PostgreSQL. `app_dr.py check` fails below 25 days.
    Unit-tested with real openssl; not yet exercised in a lab acceptance run.
  - *Replication CA: open.* It lasts 10 years and nothing renews it. The DR
    check fails on both hosts below 180 days. Missing: a tested procedure
    that replaces it on both hosts and issues the primary a new certificate,
    without a window where the standby trusts neither.
  - *nginx: partly done.* On a single host the nightly backup run checks
    the certificate: in local mode it fails below 30 days (a restart
    renews it), in provided mode it fails below 30 (T4); the request,
    the CA step and the install are by hand. A DR pair in provided mode is
    checked by `app_dr.py check` on both hosts. Open: local mode
    still renews only at a restart, and a DR pair in local mode checks
    nothing.
## Monitoring and backup routine

WAL on the primary is bounded (`max_slot_wal_keep_size=1GB`): a standby that is
down too long invalidates its slot, `cluster-status` reports it, and
`app-ops reseed-standby` copies the standby again while the primary serves
(accepted in run 44). On both DR hosts `platform-dr-check.timer` reports that, failed WAL
archiving and a filling disk as a failed unit every 15 minutes, and
`platform-backup.timer` takes and prunes the primary's base backups every night
(accepted in run 32; deploy/dr/README.md). A single host gets the same nightly
backup from `install.sh` and restores to last night, and the WAL archive
survives a power loss (both accepted in run 35). `app_backup.py --app A
restore --target-time T` restores to a time from the newest base backup
before it (accepted in run 40).

- **M3. Regular restore tests.** *[optional]* *[docs done 2026-10-09]* The manual
  monthly drill is docs/runbooks/restore-test.md; a scheduled one stays open. A backup that was never restored
  is not proven. Run the existing disposable PITR restore on a schedule (for
  example weekly) and compare it with a known point, or document a manual
  monthly restore test instead.

## Logging

Without an assistant, the logs must tell an operator what happened, where and
why. The seven workloads already log to journald (`LogDriver=journald`), and
`promotion.json` records promotions. The Python tools and backends do not log.

- **L3. Backend logging.** *[new]* *[optional]* The example apps, not the
  core of the repository. The backends log almost nothing, and `logLevel`
  in `values.yaml` becomes `LOG_LEVEL` in each app's ConfigMap, but no
  backend reads it. Use it (or remove it), and log rejected tokens with the
  reason (never the token), database errors with context, and changes with
  the user's `sub`. A backend should also refuse to start without its OIDC
  settings, instead of answering every request with "invalid token".
- **L4. Persistent, bounded journald.** *[config]* *[done 2026-10-09; operator:
  the lab VMs get it with the next `prepare-agent-snapshots.sh` (K1)]* docs/LOGGING.md
  gives the drop-in; the readiness check warns without `/var/log/journal`. Whether logs survive a
  reboot depends on journald storage on the hosts, which is neither set nor
  documented, and nothing bounds size or age. Configure persistent storage
  with limits, and document it.
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
- **D8. A hostname in acceptance.** *[decision]* A public hostname other than
  the default is covered by unit tests only. Decide whether acceptance gets a
  step that installs the primary with one and checks it after failover.
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
  `app-quarantine.sh`, installed root-owned under `/opt/platform`, and ship it in
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
## Firewalls

The guest firewalld rules and the Proxmox quarantine are both needed: the
first lets only the client and the peer in, the second fences an old primary.
What is weak is how they are checked and switched.

- **W2. Quarantine as one tool.** *[decision]* The phase 5 rehearsal and phase
  9 switch the Proxmox VM firewall, links and rules in many separate steps.
  `acceptance.py` now has `do quarantine-profile` and `do quarantine-stop` for
  the lab. A product command that applies, lifts and verifies the whole
  profile only makes sense if the real sites run Proxmox; decide that first.
- **W4. Tool-owned guest rules.** *[new]* The firewalld rules are added by
  fixed commands in phases 3, 4, 7 and 9, tied to fixed addresses. Let a tool
  add or at least verify them (with the app-ops zone and runtime check) before
  each DR command.
## Code structure

The owner's priority: the installer and DR code must be easy to understand
and get into. The long CLI dispatches stay as they are: they read top to
bottom.

The todo and notes backends are deliberately separate examples. The
repository is about Podman, the installer and DR; the apps are there so the
installer and DR have two independent apps, each with its own database,
roles, secrets and images, to install, replicate and fail over. Sharing code
between them is not a goal: each app should read on its own.

## Tests and CI

The fakes check commands and order, not real SQL or Podman behaviour; CI's
full-stack job and acceptance cover that.

- **E5. Browser tests for failure.** *[optional]* An expired session and a real
  token refresh against Keycloak, and what the user sees when the backend or
  Keycloak is down.
- **Q1. Replication in CI.** *[optional]* *[decision]* Stream between two
  PostgreSQL instances on one runner, so replication is tested before the lab.
  The full-stack job already covers the single host.
- **Q2. Mutation testing in CI.** *[optional]* It runs only from the default
  branch (`schedule` and `workflow_dispatch`); it starts working after the
  merge.
- **Q3. Security update routine.** *[config]* *[done 2026-10-09; owner: put
  `.github/dependabot.yml` on the default branch, `feature/minimal-todo`, where
  Dependabot reads it]* Weekly pull requests against `feature/podman-kube` for
  pip, the Containerfiles and Actions; PostgreSQL and the CI tools by hand. Python packages, base images
  (Python, nginx, Keycloak, PostgreSQL) and GitHub Actions are all pinned, but
  nothing reports a security fix. Enable Dependabot for pip, container images
  and Actions, so each update arrives as a pull request that CI tests.
- **Q4. Secret scanning.** *[optional]* GitHub secret scanning, or a
  gitleaks/trufflehog run, on top of the pattern search already done.

## File-based secrets and certificates (demo)

Concepts for another installer, tested here first. That installer runs larger
applications and Duende IdentityServer, which signs its tokens with a key from
a password-protected PFX file and needs certificates to trust; handling the PFX
as a secret has proved hard there. Keycloak can do the same thing (decided
2026-10-06), so the demo is not contrived: the identity server signs tokens
with an organisation key from a PFX, and an app trusts that key only because
its certificate was issued by the organisation's CA. The Podman mechanics are
the same whichever identity server reads the files.

- **X1. Keycloak signs with a PFX secret; Notes trusts it through a CA volume.**
  *[new]* Two parts, each with its own acceptance run: X1a on one host, X1b
  for DR. Start with a short CI spike that proves the Keycloak part
  (step 2) before building the rest; if it fails, fall back to signing in
  the Notes backend. The spike is `deploy/scripts/dev/spike_keycloak_pfx.py`
  (CI job "Keycloak PFX spike"); its RESULT lines answer step 2's questions.
  *Spike passed (2026-10-08, Keycloak 26.7.1, Podman 5.7):* the PFX
  (OpenSSL 3 default, AES-256/PBKDF2) arrives byte for byte from a Kube
  file secret, `defaultMode: 0440` gives owner 0:0 mode 0440, readable by
  Keycloak's uid 1000 in group 0. Keycloak loads a keystore only from the
  realm's own directory: mount it at `/opt/keycloak/data/todo`, anywhere
  else is refused ("not under the realm directory"). The password can come
  from Keycloak's file vault (`KC_VAULT=file`, a second file secret named
  `<realm>_<key>`, `keystorePassword: ${vault.<key>}`), so it never crosses
  the admin API, which returns only the expression. Tokens are then signed
  with the PFX key (kid, JWKS `x5c`, openssl verifies), and a wrong
  password or missing file fails adding the key with HTTP 400.
  1. *Generation, in Python.* The installer, not a Bash script, makes the
     demo PKI before secrets are provisioned and the pods start, by running
     `openssl` (present on the hosts; the offline installer has only the
     standard library, so no `cryptography` there): a demo CA, a signing key
     and certificate issued by it, and a PKCS#12 file (`.pfx`) holding the key
     and certificate under a random password. The files live in one
     directory under the install user's home (for example
     `~/.local/share/todo-pki`, 0700, files 0600), never in images or the
     offline bundle. Every install keeps existing valid material: key,
     certificate and password never change on a reinstall. Incomplete or
     inconsistent files stop the install with a clear message, and so do
     existing secrets with a missing or different directory: the installer
     never makes new keys silently next to old secrets; changing the key is
     a deliberate act.
  2. *The PFX as a file secret for Keycloak.* The PFX becomes a Kube secret
     built from the file's bytes (base64; the project's secret helpers
     handle text only, and `--showsecret`, newline stripping or UTF-8 would
     corrupt it), mounted as a file in the Keycloak container with a clear
     path, readable by Keycloak's user only. Its password is a separate raw
     Podman secret and Kube secret, as the other credentials
     (`secrets.create_kube`), never in a manifest, plain environment value,
     log or API response. Existing secrets are reused, never overwritten,
     and a difference from the files stops the install. `keycloak.configure`
     adds a realm key of Keycloak's `java-keystore` provider (PKCS#12, alias,
     password) with a higher priority than the generated RSA key, so the
     realm's tokens are signed with the PFX key. Wrong password or missing
     file: Keycloak cannot load the key, and the install stops there.
  3. *The CA certificate in a plain PVC volume for Notes.* Only the public CA
     certificate goes into a new named volume, `notes-root-cert-data`, before
     the Notes pod starts (`podman volume import` of a tar holding just that
     file), mounted read-only through a `persistentVolumeClaim` in the Notes
     backend. Its content changes only while Notes is stopped, and a
     reinstall with the same CA leaves it alone. When the Notes backend
     validates a token, it also checks that the signing certificate Keycloak
     publishes in its JWKS (`x5c`) was issued by that CA, and rejects the
     token otherwise. The Notes backend uses `cryptography` in its own image
     for the chain check; the standard library has none.
  4. *Visible result.* After login, Notes shows a short status: "Token
     signert med organisasjonens nøkkel, sertifikat kontrollert mot CA",
     with the signing certificate's subject, expiry and fingerprint. A wrong
     CA in the volume makes Notes reject the token with a clear message. No
     general signing API.
  5. *Development and production.* Direct `podman kube play` (`dev-up.sh`,
     `dev-down.sh` cleans the volume and secrets up) and Quadlet both get the
     volume and secrets before the pods start. `deploy/manifests/app.yaml.j2`
     is shared by both apps, so the mounts follow a per-app switch in the
     registry (`apps.py`), not a copy of the template.
  6. *DR (X1b), decided 2026-10-06.* The PFX (as base64 text: the DR copy is
     JSON text), its password and the CA certificate are kept as raw Podman
     secrets on the primary and join `transfer.transfer_names()`, so
     bootstrap, rebuild and reseed copy them. Keycloak in Trondheim then
     signs with the same key, and a signature made in Oslo still verifies
     after a failover. `deploy-promoted` requires them, makes the Kube
     secrets and creates `notes-root-cert-data` before Keycloak and Notes
     start, through an installer function (the DR code may import the
     installer, never the other way). The DR check's "Ready to take over"
     counts them. Until X1b, a promoted host without them must refuse with a
     clear message, never report a working failover. The base backup and WAL
     archive do not hold these files (SECRETS.md says so); a single-host
     restore keeps them on the host.
  7. *Tests.* Unit tests for generation (keep, refuse, never regenerate next
     to existing secrets), the binary-safe secret and the volume step; CI
     with real Podman: first install, a second install with the same key,
     password and secret identity, a recreated pod keeping volume and
     secrets, a wrong password and a missing file stopping the install
     before the pods start, a wrong CA rejected by Notes, and no private file
     or password in the bundle; both `podman kube play` and Quadlet. Then
     acceptance: the status after login in phase 3 (X1a) and, after failover,
     the same certificate fingerprint on `.108` in phase 7 (X1b).
- **X2. Load the PFX in .NET.** *[optional]* The part only Duende has: a
  minimal .NET container that loads the same PFX from the same file secret
  and password (with `X509KeyStorageFlags.EphemeralKeySet`, as .NET in a
  Linux container usually needs) and prints the certificate's fingerprint.
  It tests the .NET side without replacing Keycloak. After X1.
  - What it covers: the line Duende relies on before `AddSigningCredential`,
    `new X509Certificate2(path, password, EphemeralKeySet)`, against the same
    Podman secret mounts: a PFX that arrives byte for byte, the file mode and
    owner the container user can read, the right password, and no need for a
    user profile key store.
  - What it does not cover: `AddSigningCredential` itself, Duende's automatic
    key management, ASP.NET data protection and Duende's database. Those need
    Duende (and its licence) and stay out of scope.

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

- **P2. A readiness stop shows its FAIL lines.** *[done; in acceptance from the next run]*
  Done 2026-10-09: the readiness check ends with `Failed checks, by section:`,
  every FAIL with its `== ...` heading, so the tail a stop shows holds them;
  a busy port 8080 names its process and pid (`ss`), for `rootlessport` the
  container, and what to do. Found 2026-10-09
  (run `2026-10-09-run-1`): when the C1a readiness check fails,
  `acceptance.py run` prints only `tail -n 20` of `logs/00-readiness.log`
  (ACCEPTANCE-AGENT.md, C1a), which is the end of the last host's section,
  and then `STOP: ... fix what it names (FAIL lines)`. The one FAIL line was
  further up, so the screen showed only PASS lines and the operator had to
  find it in the log. On a stop, print every FAIL line of the last attempt
  with the `== ...` heading it belongs to (or end the check with a summary of
  its FAIL lines), so the stop names what to fix.
  The FAIL in that run was `Local port 8080 free`, held by `rootlessport`, a
  rootless Podman container on the client that the run had not started (the
  run only opens its own SSH tunnel on that port, in provision-user.sh, and
  closes it). So the check should also name what holds the port: the
  process and its pid (`ss -ltnp 'sport = :8080'`, no new tool), and for
  `rootlessport` the container that publishes it (from `podman ps --format
  '{{.Names}} {{.Ports}}'`), with a hint: an `ssh` process is a tunnel left from an
  earlier run, a container is this user's own Podman stack (a dev or server
  install on the client) to stop for the run.

- **P3. Client trust without a sudo prompt.** *[new]* Found 2026-10-09
  (run `2026-10-09-run-2`): the run stops twice for the operator's sudo
  password, before `03-4a` and before `07-5` (the client trust, C9.4), and a
  password typed late times out (`sudo: timed out`), so /etc/hosts was not
  changed and the run stopped. The client needs root for three things only:
  replace the `todo.test`/`notes.test` line in /etc/hosts, copy the serving
  host's CA to `/usr/local/share/ca-certificates/platform-nginx-root.crt`, and
  run `update-ca-certificates` (C9.4 and `deploy/scripts/lab/trust-serving-ca.sh`).
  Put those three in one small root-owned script with fixed paths, for
  example `/usr/local/sbin/todo-lab-client-trust IP CA_FILE`, which checks
  that IP is an IPv4 address and CA_FILE a single self-signed CA
  certificate, and give the operator's user `NOPASSWD` for that script only
  (one sudoers line, as `platform-ca-sign` in docs/TLS.md). Fetching the CA and
  checking its fingerprint stay unprivileged, and the Chromium NSS import
  needs no root. Set it up once in Part A of ACCEPTANCE-AGENT.md; the
  readiness check (C1a) verifies it with `sudo -n -l`. Then the run needs no
  prompt at all, and no broad root stays cached during it.
  Interim, done 2026-10-09: `acceptance.py run` asks once, at the start
  (`sudo -v`), renews the timestamp every minute (`sudo -n -v`) and drops it
  at the end (`sudo -k`); the client trust runs with `sudo -n`. That removes
  the waits (and the person's reaction time from the failover time), but
  keeps broad root cached for the 40 minutes, which this item removes.

## Lab housekeeping (operator)

- **K1.** *[optional]* *[operator]* Rebuild the `clean-agent` snapshots with
  `prepare-agent-snapshots.sh`, so they hold `python3-pyyaml` and no longer
  the unused `python3-jinja2` an older version of the script installed.
- **K2.** *[operator]* Remove the old `todo-lab-ca-*` nicknames from the
  client NSS database (`certutil -D -d sql:$HOME/.pki/nssdb -n NAME`).
- **K3.** *[operator]* Review and remove the old rule without a comment
  (tcp 5432 from `.111`) on VM 108; run 44 still found it. The three
  `todo-quarantine-*` rules and DROP policies on VM 107 may stay: every run
  replaces them (rule 7's exception).
