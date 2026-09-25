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

## After a CLEAN PASS with app-ops: retire Ansible

3. Delete `deploy/ansible` and `ansible.cfg`, and take them out of the
   operations package.
4. Remove the Ansible CI jobs and the tests that run playbooks.
5. Rewrite `ACCEPTANCE.md`, `PROXMOX-QUARANTINE.md` and
   `ACCEPTANCE-TROUBLESHOOTING.md` for app-ops, then merge
   `ACCEPTANCE-APP-OPS.md` into `ACCEPTANCE.md`, so that one guide remains.
6. Update AGENTS.md: "Python installer for single-host, app-ops (plain SSH) for
   DR/multi-host", and the rule that DR installs workloads through
   `install-workload.yml`.

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
14. Optional: GitHub secret scanning, or a gitleaks/trufflehog run, on top of
    the pattern search already done.

## Lab housekeeping (operator)

15. Rebuild the `clean-agent` snapshots with `prepare-agent-snapshots.sh`, so
    they contain `python3-jinja2` and `python3-pyyaml` (every run installs them
    as a recorded deviation today).
16. Remove the old `todo-lab-ca-*` nicknames from the client NSS database
    (`certutil -D -d sql:$HOME/.pki/nssdb -n NAME`).
17. Review and remove the old Proxmox firewall rules: three
    `todo-quarantine-*` rules and DROP policies on VM 107, and one rule without
    a comment (tcp 5432 from `.111`) on VM 108.
