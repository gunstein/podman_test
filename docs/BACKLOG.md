# Backlog

Agreed work that waits until the two-VM acceptance run with app-ops has finished.
Changing checked code during a run would test a different revision from the one
in the kickoff message. Remove an item when its change is merged.

## Order

1. The acceptance run on `1b1d345` for a CLEAN PASS: a clean baseline to
   compare against.
2. Security: T1 (encrypted replication between the two sites), H1 (Keycloak
   brute force) and H2 (security headers).
3. A real failover between the sites: T3 (fencing without the failed site's
   hypervisor), T4 (one CA for both sites) and T5 (moving the names).
4. What operation needs: U1 (updating a replicated pair), M1 and M2 (alerts,
   scheduled backups with pruning), L1 and L2 (command logging, failure
   reasons).
5. Retire Ansible (R0, R0b, 3-6) once there is a CLEAN PASS.
6. fapolicyd (F), firewalls (W) and data checks (C).
7. DR code structure and the rest.

The real setup has two machines and no third, on separate hardware at separate
physical sites. D2 and L6 are therefore designed for two hosts that keep copies
for each other, and everything between them crosses a network between sites
(T1).

## Between the two sites

- **T1. Encrypt replication.** `pg_hba.conf` uses `host`, not `hostssl`,
  PostgreSQL has no TLS, and `primary_conninfo` sets no `sslmode`. SCRAM keeps
  the password off the wire, but the WAL stream (all content of all three
  databases, including Keycloak users and password hashes) travels in clear
  text. Acceptable inside one room; not between sites. Turn on TLS in
  PostgreSQL with certificates from a small project CA, require `hostssl` for
  replication, and connect with `sslmode=verify-full`. The backup copy in D2
  must be encrypted in transit too.
- **T2. Latency and bandwidth between sites.** Asynchronous replication across
  sites can lag further behind than in the lab, which widens what a failover
  can lose (C4). Measure lag over the real link, check that the RPO target of
  30 seconds holds, and that timeouts (SSH, `connect_timeout`) suit it.
- **T3. Fencing when the other site does not answer.** Acceptance fences the
  old primary through the Proxmox API (power off, links down, ports checked),
  which needs access to the failed site's hypervisor. With a whole site gone
  or cut off, that access may be missing, and an isolated old primary could go
  on accepting writes (split-brain). Write a procedure for how the operator
  *knows* the old site is fenced (confirmation from someone on site, power
  removed, the network closed from the surviving side), and for what to do when
  that site comes back with its old primary. The quarantine covers the return
  only when the hypervisor is reachable.
- **T4. One CA for both sites.** The promoted host creates its own CA, so
  after a failover every user's browser shows certificate errors until the new
  CA is rolled out (acceptance trusted the new CA on its one client by hand).
  Share one CA between the sites, synchronised like the other DR secrets over
  an encrypted link (T1), or use certificates from an existing PKI.
- **T5. Pointing users at the other site.** Clients use `todo.test` and
  `notes.test`; acceptance edits `/etc/hosts` on one client. Write down how the
  names move to the surviving site in the real setup (a DNS change with a
  short TTL, a floating address or similar), who does it, and how long it
  takes. Without it, failover is done but nobody reaches the service.

## Updates and time

- **U1. An update path for a replicated pair.** The installer now refuses a
  host with replication, which is safer, but it leaves no supported way to roll
  out new application images or configuration after DR is set up: `install.sh`
  refuses on the primary, and `deploy-promoted-application` covers only a
  promoted host. Updates from item 14 therefore have no way into operation.
  Add an app-ops command that updates the application tier on the current
  primary while keeping the LAN publication, a fixed order for PostgreSQL
  minor updates (standby first), and a plan for major upgrades such as 17 to
  18, which cannot stream between versions.
- **U2. TLS renewal while running.** The server certificate lasts 397 days, and
  `proxy-entrypoint.sh` issues a new one only when nginx starts with fewer than
  30 days left. nginx running for over a year without a restart serves an
  expired certificate, and nothing warns. Check the expiry in M1, and add a
  renewal that does not need a full service restart.
- **U3. Time synchronisation.** Token expiry, TLS and log timestamps depend on
  correct clocks on both hosts. Check that chrony (or another time service) is
  active in the preflight and in acceptance phase 1, and document it.

## Security hardening

The containers already run as non-root users, with
`allowPrivilegeEscalation: false` and every capability dropped, and TLS is
limited to 1.2 and 1.3.

- **H1. Brute-force protection in Keycloak.** `keycloak/todo-realm.json` sets
  neither `bruteForceProtected` nor a password policy, and Keycloak leaves both
  off by default, so anyone who reaches the login page can guess passwords
  without limit. Turn on temporary lockout after repeated failures and set a
  password policy in the realm import.
- **H2. HTTP security headers.** The shared nginx sets only
  `X-Content-Type-Options`. Add HSTS, a Content-Security-Policy and
  `frame-ancestors` in `shared-proxy.yaml.j2`. The CSP must allow what the
  Keycloak adapter needs, so run the browser tests afterwards.
- **H3. Vulnerability scanning of images.** Dependabot (item 14) reports new
  versions, not known vulnerabilities in the packages inside the base images.
  Scan the built images in CI (for example with Trivy); a CI-only tool, never
  installed on target hosts.
- **H4. Optional: read-only root filesystems.** Set `readOnlyRootFilesystem`
  where a container allows it, with writable volumes only where needed. Extra
  hardening, not a gap.

## fapolicyd

First check `grep -E '^\s*integrity' /etc/fapolicyd/fapolicyd.conf` on both
VMs (read-only). With the default `integrity = none`, fapolicyd trusts a path
whatever its current contents, which makes F2 and F4 real weaknesses.

- **F1. Correct the docs.** FAPOLICYD.md says trust is tied to path, size and
  hash. That only holds when `integrity` is `size`, `sha256` or `ima`; waiting
  for the exact `--dump-db` lines checks the database, not enforcement. Say
  what holds with and without an integrity check.
- **F2. Never trust user-writable files.** The controller runs app_ops and
  app_installer from `~/todo-operations`, `install_trusted` trusts those
  sources on the controller, and the offline install trusts the extracted
  bundle's Python in `$HOME`. Run all trusted Python from root-owned copies
  (install to `/opt/todo/lib` first, then run from there) on the controller
  and in the offline install too, so no trust entry points into a home
  directory.
- **F3. A root-owned trust helper.** `trust-files.sh` runs as text passed to
  `sh -c` from the user's operations package, so fapolicyd never checks it.
  Install it once as a root-owned file with its own trust, and run that.
- **F4. Remove stale trust.** Replacing, renaming or deleting a file leaves its
  trust entry behind (for example the old `todo_*` tools and old bundle
  extractions); cleanup is manual today. Remove entries when files are
  replaced or retired.
- **F5. One trust file.** The code and docs use `todo`, `app-installer` and
  `todo-component`. Use one name, so it is clear what the project trusts.
- **F6. State the lab limit.** With `NOPASSWD: ALL` the service user can do
  anything as root, so acceptance does not test fapolicyd as a barrier against
  that user. Say so in the acceptance docs.

## Firewalls

The guest firewalld rules and the Proxmox quarantine are both needed: the
first lets only the client and the peer in, the second fences an old primary.
What is weak is how they are checked and switched.

- **W2. Quarantine as one tool.** The phase 5 rehearsal and phase 9 switch the
  Proxmox VM firewall, links and rules in many separate API calls; skipping
  one left VM 107 quarantined in run 2. Add one idempotent command that applies,
  lifts and verifies the whole profile, so lifting and checking cannot be
  forgotten halfway.
- **W3. An exact lab baseline.** Proxmox firewall state is not part of a VM
  snapshot, and leftovers from earlier runs stay behind. Phase 1 deletes them
  by comment prefix. Add a script that resets both VMs to an exact expected
  rule list and reports anything else, and say clearly that snapshots do not
  cover this state.
- **W4. Tool-owned guest rules.** The firewalld rules are copy-and-paste
  commands in phases 3, 4, 7 and 9, tied to fixed addresses. Let a tool add
  or at least verify them (with the app-ops zone and runtime check) before each
  DR command.
- **W5. Note the node-wide effect.** VM rules need the datacenter and node
  firewall on, which also changes access to the Proxmox host itself. State this
  in the agent guide's preparation part.

## After a CLEAN PASS with app-ops: retire Ansible

- **R0. Record the result first.** Add the run's evidence as
  `docs/history/ACCEPTANCE-<short-sha>.md`, as for earlier runs, and update the
  verdict and run list in `PROJECT.md#acceptance`, which AGENTS.md points to.
  Bring the rest of `PROJECT.md` up to date too: app-ops, the new runs and this
  backlog. Check and state whether the two standby-rebuild defects that the
  `3fb897f` record names are fixed. Move `docs/ACCEPTANCE-3fb897f.md` into
  `docs/history/` with the other evidence, changing only the links to it.
- **R0b. Replace `deploy.yml` and `uninstall.yml`.** They were never ported to
  app-ops because they only wrap `app_installer install` and `uninstall`. Show
  those direct commands in the guides before the playbooks go.

3. Delete `deploy/ansible` and `ansible.cfg`, and take them out of the
   operations package.
4. Remove the Ansible CI jobs and the tests that run playbooks.
5. Rewrite `ACCEPTANCE.md`, `PROXMOX-QUARANTINE.md` and
   `ACCEPTANCE-TROUBLESHOOTING.md` for app-ops, then merge
   `ACCEPTANCE-APP-OPS.md` into `ACCEPTANCE.md`, so that one guide remains.
6. Update AGENTS.md: "Python installer for single-host, app-ops (plain SSH) for
   DR/multi-host", and the rule that DR installs workloads through
   `install-workload.yml`.

## Logging

Without an assistant, the logs must tell an operator what happened, where and
why. The seven workloads already log to journald (`LogDriver=journald`), and
`promotion.json` records promotions. The Python tools and backends do not log.

- **L1. Log every operations and DR command.** No Python code uses `logging`;
  the installer, `app_dr.py`, `app_backup.py` and app-ops print to the terminal
  only, without timestamps, and keep nothing. Add one small shared helper on
  the standard library that writes to journald (for example through
  `logger -t app-ops`): timestamp, host, command, database, result and duration,
  never a secret. app-ops also keeps one log file per run on the controller.
- **L2. Keep the failure reason in the installer.** `commands.run` reports only
  `podman secret failed (exit 1)` and drops stderr, so the command must be rerun
  by hand to see why. Include the stderr tail, except for commands that can
  print secrets (such as `podman secret inspect`), as app-ops and `app_dr.py`
  already do.
- **L3. Backend logging.** The backends log almost nothing themselves, and
  `LOG_LEVEL` from `values.yaml` is set but never used (uvicorn runs at its
  default). Use it, and log rejected tokens with the reason (never the token),
  database errors with context, and changes with the user's `sub`.
- **L4. Persistent, bounded journald.** Whether logs survive a reboot depends
  on journald storage on the hosts, which is neither set nor documented, and
  nothing bounds size or age. Configure persistent storage with limits, and
  document it.
- **L5. `docs/LOGGING.md`.** One page with where each log lives and ready
  commands per workload and tool, including the rootless
  `journalctl _SYSTEMD_USER_UNIT=...` form (`journalctl --user` can show nothing).
- **L6. Logs across the two hosts.** Each VM keeps its own journal, so after
  a failover the history is split, and in a real disaster one host may be
  gone with its logs. With two machines and no third: let each host forward its
  journal to the other (for example `systemd-journal-upload`/`-remote`), so the
  surviving host also holds the lost host's logs up to the moment it was lost.
  Testable in the lab with the two VMs.

## Monitoring and backup routine

WAL on the primary is bounded (`max_slot_wal_keep_size=1GB`): a standby that is
down too long invalidates its slot, `cluster-status` reports it, and the standby
is rebuilt. What is missing is anything that tells the operator.

- **M1. Scheduled checks that alert.** Today an operator only learns that
  replication stopped, a slot was invalidated, WAL archiving fails or a disk
  fills up by running `app_dr.py status`, `app_backup.py status` or
  `cluster-status` by hand; ARCHITECTURE.md says lag and invalidated slots need
  monitoring. Add a systemd timer that runs these checks regularly, so a failed
  check becomes a failed unit in the journal, with an optional `OnFailure=`
  mail. No new dependency.
- **M2. Scheduled backups and pruning.** Backups are taken only when someone
  runs `app_backup.py create`, and the archive copies every WAL file into the
  backup volume while nothing removes old base backups or WAL, so the primary's
  disk slowly fills. Add a timer for backups on a fixed schedule, and pruning
  that keeps the last N base backups and only the WAL they need.
- **M3. Regular restore tests.** A backup that was never restored is not
  proven. Run the existing disposable PITR restore on a schedule (for example
  weekly) and compare it with a known point.

## Data checks in acceptance

Acceptance proves replication state for all three databases (streaming, slot,
zero lag, equal LSNs), but checks content only through marker rows in todo and
notes. Keycloak's database is checked only indirectly, through a login on the
promoted primary.

- **C1. A content fingerprint per database.** A read-only command (in
  `app_installer`, called by app-ops) that gives, for every table, the row count
  and a hash over its rows in a fixed order. Compare primary and standby for
  all three databases after bootstrap (phase 4), just before fencing (phase 6,
  while both are reachable) and after the rebuild (phase 9).
- **C2. A direct Keycloak marker.** For example an attribute on the test user,
  read with SQL on the standby like the todo and notes markers, including on
  the rebuilt standby in phase 9.
- **C3. PITR for Keycloak too.** Phase 8 backs up and checks archiving for all
  three databases but restores only todo and notes. Restore Keycloak's
  database as well, and compare a known value before and after the restore
  point.
- **C4. State what asynchronous replication can lose.** Acceptance fences only
  after the last marker has reached the standby, so it shows failover works,
  not the worst-case loss of a crash while writing. Say plainly in
  ACCEPTANCE.md and ARCHITECTURE.md that the last transactions can be lost
  (the RPO), and that this is a design choice.

## Operations and DR decisions

- **D1. Sudo with a password in app-ops.** app-ops needs `NOPASSWD` today, and
  acceptance grants `NOPASSWD: ALL` (see F6). As agreed when passwords were
  dropped, add password support later by running every privileged command
  through `/bin/sh`, so a narrow NOPASSWD rule can never match it and a
  password line can never become a command's stdin.
- **D2. Backups that survive losing a machine.** Base backups and WAL live on
  the same VM as the database. The standby holds today's data, but not the
  history: a mistaken delete replicates within seconds, and only PITR from the
  backup undoes it, from a backup that was on the machine that was lost.
  Decided: the two machines are on separate hardware at separate sites, so the
  two hosts keep copies for each other. Copy the base backups and WAL archive
  from the primary into a separate volume on the standby host, encrypted in
  transit (T1), and reverse the direction after a failover. Testable in the lab
  with the two VMs, although they share one physical host there.
- **D3. `deploy-promoted-application` runs only on the promoted host.** It
  refuses unless that host is the machine running app-ops. Either lift the
  limit or document it as deliberate in `deploy/ops/README.md`.

## DR code structure

7. **One place for paths and constants** in `settings.py`: the
   `todo-kube-runtime` directory (14 places), `/opt/todo` (11), the promotion
   record path (6), the service port 8443 and the RPO of 30 seconds in app_ops.
8. **One way to run commands and SQL.** `app_dr.py` and `app_backup.py` have
   their own command runners and error types, and `app_backup.py` spells out
   nine `psql` calls; use `replication.sql()` and one shared runner.
9. **Smaller units.** Split `app_backup.py` (archiving, backup, restore) and
   `replication.py` (bootstrap/reseed, status checks). Give each
   `app_installer/cli.py` and `app_backup.py` command its own small function.
10. **Small cleanups.** Rename `TodoDr` and `TodoBackup`; replace the manual
    `sys.path` setup in the scripts; describe the JSON contract between
    app_ops and app_installer.
11. **Backend duplication.** `todo-backend` and `notes-backend` have identical
    `migrate.py` and near-identical `setup_roles.py` and `main.py`. Share them;
    this touches the image builds.

## Tests

12. **A whole-stack CI job** (decision needed): install the seven pods with
    real Podman and stream between two PostgreSQL instances on one runner. The
    fakes check commands and order, but only acceptance proves real behaviour
    today.
13. **Mutation testing in CI** runs only from the default branch
    (`schedule` and `workflow_dispatch`); it starts working after the merge.
14. **Security update routine.** Python packages, base images (Python,
    nginx, Keycloak, PostgreSQL) and GitHub Actions are all pinned, but nothing
    reports a security fix. Enable Dependabot for pip, container images and
    Actions, so each update arrives as a pull request that CI tests. No runtime
    dependency is added.
15. Optional: GitHub secret scanning, or a gitleaks/trufflehog run, on top of
    the pattern search already done.

## Lab housekeeping (operator)

16. Rebuild the `clean-agent` snapshots with `prepare-agent-snapshots.sh`, so
    they contain `python3-jinja2` and `python3-pyyaml` (every run installs them
    as a recorded deviation today).
17. Remove the old `todo-lab-ca-*` nicknames from the client NSS database
    (`certutil -D -d sql:$HOME/.pki/nssdb -n NAME`).
18. Review and remove the old Proxmox firewall rules: three
    `todo-quarantine-*` rules and DROP policies on VM 107, and one rule without
    a comment (tcp 5432 from `.111`) on VM 108.
