# Project status

## Goal

Learn rootless Podman by building and deploying a small Todo application.

The project should demonstrate Podman, Quadlet, Ansible and eventually offline installation in small, understandable steps.

## Current milestone

Clean M12-M16 Oracle Linux drill in progress. M12 through M14 have passed on
newly installed VMs, including controlled promotion, application failover,
idempotence and reboot. M15 is next. M16 is implemented, but its live
restore-redundancy drill is still pending.

## Live clean-deployment checkpoint — 2026-08-29–30

Resume from this exact state:

- `todo-primary` is `192.168.0.102`; `todo-standby` is `192.168.0.108`.
- Both are independently installed Oracle Linux 9.8 VMs with 4 GiB RAM,
  SELinux enforcing, active `fapolicyd`, rootless Podman, user lingering and
  clean Proxmox baseline snapshots.
- Primary-to-standby SSH uses primary's RSA key and passes with
  `BatchMode=yes`.
- A clean M12 offline install passed HTTPS, Keycloak login, authenticated Todo
  creation, reboot and an idempotent rerun with `changed=0`.
- The database contains `M12 clean deployment test` and
  `M13 streaming replication test`.
- M13 bootstrap passed from scratch. Primary reports the physical
  `todo_standby` slot as active and `streaming|async` with zero measured lag;
  standby reports recovery mode and matching receive/replay LSNs.
- The standby resumed recovery and streaming after reboot, and both Todo rows
  were verified directly in its read-only database.
- M13.5 was installed separately after replication. On standby,
  `todo_dr.py status` reports an active healthy standby, read-only database,
  zero local apply lag and reachable primary `192.168.0.102:5432`.
- The old `todo-primary` is fenced in Proxmox, stopped and has start-at-boot
  disabled. Keep it that way until M16 deliberately destroys and re-seeds its
  old database state.
- `todo-standby` was promoted in approximately two seconds and verified as
  `f|off` with a rolled-back writable transaction.
- M14 deployed the full application tier on the promoted host without database
  bootstrap, migrations or grants. `todo.test` now maps to `192.168.0.108`.
- HTTPS health/readiness, the stable Keycloak issuer, the replicated Keycloak
  user and an authenticated `M14 clean failover test` write all passed.
- A second M14 deployment completed with `changed=0`; after reboot all four
  services were active, PostgreSQL remained writable and client HTTPS checks
  passed.

Exact next safe operation:

1. Keep old `todo-primary` fenced and powered off.
2. Run the M15 backup/WAL/PITR drill on promoted `todo-standby` at
   `192.168.0.108`.
3. After M15 passes, use M16 to quarantine and re-seed the old primary as the
   new database-only standby; do not boot its old database normally.

New issues found during this drill:

- The M13.5 controller needs exact `fapolicyd` trust for both
  `scripts/todo_dr.py` and `ansible/roles/todo_dr/tasks/main.yml`. The latter
  contains the pipelined inline Python installer and was denied as executable
  content. Both exact files are now recorded in primary's `todo-dr-source`
  trust file. The runbook and `FAPOLICYD.md` now document both exact paths; do
  not trust the complete extracted directory.
- Standby's deployed `~/.config/todo/todo_dr.py` and `todo-dr.json` are recorded
  in its exact `todo-dr` trust file, and the local status command succeeds.
- The clean drill exposed that the M12 builder included top-level M13-M16
  playbooks without their roles. The builder is now restricted to M12's usable
  `deploy.yml`, `uninstall.yml`, inventory and requirements files.
- When replacing both example addresses on one inventory line with `sed`, use a
  global replacement or update `todo_node_address` explicitly; the first test
  correctly failed read-only preflight when that second value remained
  `192.0.2.11`.

## Completed

- `GET /health` returns `{"status": "ok"}`.
- PostgreSQL runs in a rootless Podman container with a named volume.
- The Todo API supports create, read, update and delete operations.
- The frontend uses plain HTML, CSS and JavaScript.
- The frontend can create, complete and delete Todos.
- FastAPI serves the frontend.
- The README contains local development instructions.
- PostgreSQL data was verified to survive a container stop and restart.
- Versioned SQL migrations support status, upgrade and single-step rollback.
- Migration upgrade and rollback were verified against a disposable test database.
- Nineteen automated backend tests cover health, readiness, frontend serving, CRUD, validation, migrations and positive and negative JWT validation.
- Tests were verified against an isolated, automatically removed PostgreSQL test container.
- Backend and frontend images build successfully and pass isolated smoke tests as UID 1000.
- PostgreSQL, backend and frontend run manually on the rootless `todo-network`.
- Backend reaches PostgreSQL through Podman DNS using the `todo-postgres` name.
- Database persistence was verified across a PostgreSQL container stop and start.
- Container logs, inspect data, routes and lifecycle were used to diagnose and repair a Netavark namespace issue.
- Liveness (`/health`) and database readiness (`/ready`) are separate endpoints.
- Caddy provides one browser-facing endpoint and routes frontend and API traffic by path.
- Full Todo CRUD was verified through Caddy.
- One Chromium E2E test verifies the complete browser Todo flow through Caddy.
- Rootless Quadlet definitions describe the network, persistent volume, PostgreSQL, migration, backend and frontend.
- Quadlet dependencies enforce PostgreSQL health, role setup, completed migrations, final runtime grants, backend startup and frontend startup in that order.
- The Quadlet lifecycle and browser flow were verified through user systemd.
- PostgreSQL, migrations and the backend receive the database password from a file-mounted rootless Podman secret.
- The secret-based deployment passed health, readiness, API and Chromium E2E checks.
- A small localhost Ansible playbook builds the images, installs Quadlet files, creates the Podman secret when missing, starts the services and verifies health and readiness.
- A second Ansible run completed with `changed=0`, confirming idempotence.
- The Ansible-deployed application passed the Chromium E2E test.
- An offline tar.gz bundle contains OCI image archives, deployment files, a target preflight check, an installer and SHA-256 checksums.
- Oracle Linux 9 offline installation was verified with SELinux enforcing, active `fapolicyd` and RPM-managed `ansible-core`.
- Offline installation was verified after removing all three local images and blocking registry and package-index access with invalid proxies.
- PostgreSQL data survived the offline reinstall.
- Caddy serves locally issued HTTPS on `https://localhost:8443` while retaining HTTP on port 8080 for development.
- Caddy's internal CA persists in a dedicated rootless Podman volume.
- TLS hostname, certificate chain, health, readiness and Chromium E2E behavior were verified.
- M1 through M10 are complete.
- Keycloak runs behind Caddy and imports a minimal `todo` realm with a public SPA client using PKCE S256.
- Todo reads remain public, while create, update and delete operations require a valid Keycloak access token.
- Keycloak uses the existing PostgreSQL service with a separate bootstrap-owned database schema.
- The temporary Keycloak bootstrap-administrator password is held in a rootless Podman secret and no users or passwords are committed.
- Public-read and authenticated CRUD browser flows were verified through Keycloak.
- A helper provisions the complete `testuser` account and runs both E2E flows without storing test passwords.
- M11 is complete.
- Direct application dependencies and base-image patch versions are pinned.
- An explicit `refresh_images=true` mode separates idempotent reuse from security refresh.
- PostgreSQL runtime access is split into non-superuser migration, backend and Keycloak roles with independent secrets.
- The migration, backend and Keycloak roles cannot inherit one another's database privileges.
- Fresh-install and upgraded-database checks verified that the Todo roles cannot access Keycloak data and the Keycloak role cannot access Todo data.
- An idempotent role-setup service upgraded the existing volume without losing Todo or Keycloak data.
- Backend, frontend and Keycloak run with no new privileges, no effective Linux capabilities and explicit PID limits.
- JWKS discovery is cached across requests and negative JWT tests cover issuer, audience, expiry, signature and algorithm.
- Build context excludes environment files, virtualenvs, archives, private keys and distribution artifacts.
- M12 backend tests, role setup, migrations, service startup, readiness, discovery and Ansible `changed=0` were verified.
- Both the public and authenticated M12 browser flows were verified after the least-privilege upgrade.
- A clean-install CI job verifies dependency installation, role bootstrap, migrations and backend tests against an empty PostgreSQL database.
- The clean role-setup and migration sequence was verified against an empty disposable PostgreSQL database.
- Default uninstall/reinstall was verified to preserve Todo data, Keycloak data and their existing credentials.
- M13 preflight verified unique hostnames, machine IDs and LAN addresses on two Oracle Linux 9.8 VMs.
- A dedicated `todo_replicator` role and Podman secret provide asynchronous PostgreSQL streaming replication.
- Primary-to-standby secret synchronization was verified through Ansible memory and SSH without plaintext secret files.
- A streamed `pg_basebackup`, physical replication slot and rootless standby Quadlet were verified with SELinux enforcing.
- Primary reports `todo_standby` as `streaming|async` with zero measured lag; standby reports recovery mode with matching receive and replay LSNs.
- A Todo written through the primary application was verified directly in the read-only standby database.
- Standby automatic boot, recovery re-entry and resumed zero-lag streaming were verified after a full VM reboot.
- The cloned standby required a one-time `podman system renumber` with all user Podman processes stopped to repair inherited runtime lock allocation; no volume data was removed.
- A standard-library Python DR tool implements local standby status, fail-safe fencing preflight and verified PostgreSQL promotion.
- Ten isolated unit tests verify successful promotion and rejection of unsafe fencing, reachability, lag and confirmation states.
- A live Oracle Linux 9.8 drill fenced primary at the VM layer, passed the local preflight with zero apply lag, promoted standby in approximately two seconds and verified `f|off` plus a rolled-back Todo write.
- The old primary remains fenced after promotion; it must not rejoin without being rebuilt as a replica of the promoted database.
- M14 loaded the staged backend, frontend and Keycloak images on promoted standby without running database bootstrap, migrations or grants.
- `https://todo.test:8443` served a stable Keycloak issuer, health, readiness and replicated public Todo data through a firewall rule restricted to the test laptop.
- Authenticated browser login created `M14 browser failover test` through the promoted application stack.
- A repeat M14 Ansible deployment completed with `changed=0`.
- After reboot, PostgreSQL, backend, Keycloak and Caddy all returned `active`; PostgreSQL remained writable with `f|off`, and HTTPS readiness plus Todo data were verified from the laptop.
- M15 configured `archive_mode=on` with a non-overwriting archive command and a separate `todo-postgres-backup` volume on the promoted Oracle Linux 9.8 host.
- A streamed physical base backup completed in approximately three seconds and passed `pg_verifybackup` with a SHA-256 backup manifest.
- A named-restore-point drill restored into the network-isolated `todo-postgres-restore` container without targeting or modifying the live data volume.
- The restored database contained `M15 before restore point` but excluded `M15 after restore point`; the writable live database retained both rows.
- Exact-confirmation cleanup removed only the disposable restore container and volume while preserving the base backup, WAL archive and live data.
- After a full VM reboot, PostgreSQL remained `f|off|on`, all four application services were active, the base backup persisted, WAL archiving resumed with zero failures, and the M15 playbook completed with `changed=0`.
- The live drill demonstrated that the same-VM archive grows continuously (337 MiB during testing); off-host transfer, retention and capacity alerts remain production requirements.
- M16 automation now quarantines and destructively re-seeds the old primary as a database-only standby; static validation is complete and the live two-VM drill is pending.

## Decisions

- Implement and understand the application before containerizing it.
- Use rootless Podman.
- Run containers manually before introducing Quadlet and Ansible.
- Prefer simple, pedagogical solutions over abstraction.
- Keep dependencies and Bash scripts minimal.
- Run PostgreSQL in a rootless Podman container during M2 instead of installing it on the host.
- Keep FastAPI running locally through M2; containerize it in M3.
- Add Keycloak last.
- Keep liveness and readiness checks separate.
- Use one Caddy-based frontend container to serve static files and proxy backend routes.
- Keep Playwright and Chromium as separate test-only dependencies.
- Mount the database password as a file from rootless Podman secret storage.
- Keep `DATABASE_URL` for local development and tests; containers use separate non-secret settings plus `DATABASE_PASSWORD_FILE`.
- Enable only the frontend unit; systemd starts the remaining application dependency chain.
- Use the frontend Quadlet as the only `default.target` entrypoint; require an administrator to enable user lingering for boot-before-login operation.
- Keep Keycloak's memory limit in `PodmanArgs` while supporting Podman 4.9; migrate to native `Memory=` when the tested baseline is raised.
- Start Ansible with one localhost playbook and only `ansible-core`; add remote deployment structure only when it is needed.
- Keep secret prompting conditional so repeat deployments remain non-interactive.
- Treat Podman, RPM/deb-managed `ansible-core`, user systemd and basic archive/checksum tools as offline target prerequisites.
- Use the operating system's trusted Ansible package on hardened targets instead of executing a bundled Python runtime from a user-writable directory.
- Build platform-specific offline bundles on a machine compatible with the target.
- Verify every bundled artifact with SHA-256 before installation.
- Use Caddy's internal CA for offline-compatible local HTTPS without automatically modifying host trust.
- Persist Caddy's private CA material in a dedicated volume and expose only its public root certificate for explicit trust.
- Never commit secrets.
- Keep authentication authorization simple: all authenticated users may write all Todos; per-user ownership is outside this demo.
- Keep browser tokens in memory and use Authorization Code with PKCE S256 for the public frontend client.
- Treat migration 002 as a pre-baseline correction: Keycloak schema lifecycle moved to bootstrap, and applied migrations are immutable from M12 onward.
- Retain Keycloak's temporary bootstrap administrator only for repeatable localhost administration and E2E setup.
- Keep Ansible responsible for host configuration and desired state.
- Use small, explicit Python tools for replication checks, promotion and failover workflows; do not build a second configuration-management framework.
- Provision identical releases and database credentials to primary and standby from one controlled source.
- Use asynchronous streaming replication with a 30-second operational RPO target; this is not a guaranteed bound after abrupt primary loss.
- Target database write availability within a 15-minute RTO.
- Require fencing of the old primary before promotion to avoid split-brain.
- Treat replication as availability protection, not as a replacement for backup and point-in-time recovery.

## Local development

From the project root:

```bash
source backend/.venv/bin/activate
export DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD"
uvicorn backend.main:app --reload
```

From `backend/`:

```bash
source .venv/bin/activate
export DATABASE_URL="host=127.0.0.1 port=5432 dbname=todo user=todo password=$TODO_DB_PASSWORD"
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000> in a browser.

## Roadmap

- M1: HTML/JavaScript + FastAPI with hardcoded Todo — completed
- M2: PostgreSQL + Todo CRUD — completed
- M2.5: Versioned SQL migrations — completed
- M2.6: Automated smoke and integration tests — completed
- M3: Containerfiles for backend and frontend — completed
- M4: Run manually with rootless Podman — completed
- M4.5: Troubleshooting and lifecycle — completed
- M5: Caddy reverse proxy — completed
- M5.5: End-to-end browser tests with Playwright for Python — completed
- M6: Quadlet and systemd — completed
- M7: Podman secrets — completed
- M8: Ansible deployment — completed
- M9: Offline installation from a `tar.gz` bundle — completed
- M10: HTTPS — completed
- M11: Keycloak authentication — completed
- M12: Security baseline and least privilege — completed
- M13: Ansible-provisioned PostgreSQL primary/standby with a dedicated replication role — completed
- M13.5: Small Python tools for replication status and controlled promotion — completed
- M14: Full application disaster recovery with stable service hostname, shared deployment state and smoke tests — completed
- M15: PostgreSQL backup, WAL archive and point-in-time recovery — completed
- M16: Rebuild the old primary as a new standby after failover — implemented; live drill pending

## Next step

Run the M16 procedure on the two Oracle Linux VMs: keep old primary fenced,
stop all Todo services there, pass read-only preflight, re-seed it from the
promoted primary, verify a replicated write and then verify standby/current
primary behavior across reboot. Do not call M16 complete until this drill passes.
