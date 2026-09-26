# Backlog

Agreed work that waits until the two-VM acceptance run with app-ops has finished.
Changing checked code during a run would test a different revision from the one
in the kickoff message. Remove an item when its change is merged.

## Safety

1. **The installer refuses a replicated host.** `install.sh` and
   `app_installer install` rewrite the database units without their LAN
   publication, which silently cuts off the standby. Refuse on a host with
   replication, promotion or backup state, as `uninstall` already does.
2. **One standby gate.** `app_dr.py preflight` checks role, LSNs and lag
   itself instead of calling `replication.require_standby`. Make it call the
   shared check, so the rule that decides whether promotion is safe exists once.

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

- **W1. A firewall check that cannot pass wrongly.** app-ops
  (`standby.require_firewall`) only queries the permanent configuration of zone
  `public`. A rule added without `--reload`, or an interface in another zone,
  still passes. Find the zone of the interface that holds the address, and
  require the rule in both the runtime and the permanent configuration.
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
  or at least verify them (with the W1 check) before each DR command.
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
- **D2. Backup to another host** (decision needed). Base backups and WAL live on
  the same VM as the database, so losing the VM loses its backups unless the
  standby survives. Either copy them to another host, or keep the limit and
  state it plainly as a design decision.
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
