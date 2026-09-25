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
