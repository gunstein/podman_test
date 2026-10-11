# Guestbook author notes

Starting revision: f6359380685ed41b18cd2053cd1b8f41a0b1b371, feature/platform.
Clean install CI for both this revision and 32c46b2 was green before work.

Role: app author. No git-history investigation, docs/history reading or
phase-log reading. Read AGENTS.md and the plan before section 10, README,
IDENTITY, installer usage and the documented example files. The session
was supplied a summary of earlier project conversations, so this is not a
strict experiment with zero prior project context; no earlier implementation
decisions were retrieved. A CI API response included commit metadata while
checking status; no commit diffs were requested.

## Questions, assumptions and places that need documentation

1. **Where to start an app.** There is no current add-an-app guide/skeleton.
   The plan's sections 5.1–5.9, IDENTITY section 6, and the Notes app's
   commented app.yaml/pod.yaml.j2 together supplied the contract. Copied the
   documented pod/login pattern and existing migration runner, retaining
   app-local source and no imports from another app. Put a short, runnable
   database-and-login skeleton in an add-an-app guide linked from README.

2. **Current fields versus planned fields.** Section 5 mixes implemented
   fields with future fields (setup tasks, realms, route container/rewrite).
   I used only the fields identified as implemented and shown in app.yaml.
   The guide should give one complete example of the currently accepted
   schema, separate from the plan.

3. **Pod variables and database settings.** Section 5.9 names app and images
   but does not enumerate app.names.resource, app.names.kube_secret,
   app.database.role, or the ConfigMap's DATABASE_* variables. The example
   pod and backend supplied these details, including mounted password files
   and the roles' overrides. Document their exact names and the config/secret
   mounts beside a minimal pod template. The frontend must get only the login
   key; IDENTITY already explains that boundary clearly.

4. **Replication port reservation.** platform.yaml lists 5432 and 5433,
   but not Keycloak's 5434. ARCHITECTURE's network table and the manual DR
   recipe explain the reservation. Chose 5435 for Guestbook. State reserved
   ports next to replicationPort in the configuration guide. The maintainer
   must include the fourth database in firewall/markers/failover/backup/PITR.

5. **Authenticated reads and anonymous checks.** Existing apps allow public
   reads, but this task says logged-in users read, write and delete. Chose
   authentication for all entry endpoints. Declared /api/entries status 401
   because platform checks are anonymous GETs, and /ready checks the migrated
   table without disclosing data. The add-app guide should show an expected
   401 check and explain that it does not prove authenticated CRUD.

6. **Deletion and ownership.** No requirement says users may delete only
   their own entries. Used IDENTITY's demo access model: any authenticated
   user may delete any entry. Stored no user identity. This choice is stated
   in the UI and README; clarify it in an app's acceptance criteria.

7. **Tests affected by adding an app.** AGENTS requires the render baseline
   to be updated for intentional changes. Adding the app intentionally adds
   its manifests/units/images, proxy routes, login settings and DR metadata.
   A guide should distinguish baseline updates from platform implementation
   changes and explain which tests assume the old example-app set.

8. **Execution environment.** This author environment has no Podman. Local
   checks can establish rendering and unit behavior, but cannot build images
   or install the stack. Existing CI installs the configured apps; the separate
   maintainer session must add authenticated Guestbook browser/DR coverage.

No clarification was requested from the operator. Assumptions above are
explicit so the maintainer can accept or correct them.

## Validation

- `ruff check .`: passed.
- `pyright`: passed with the same platform tooling dependencies as CI
  (Jinja2/PyYAML, without app dependencies). Installing psycopg into that
  environment exposes an existing typing error in apply_database_roles.py;
  no platform code was changed to hide it.
- Guestbook backend: 18 database-free tests passed.
- Render baseline: all 3 tests passed after the intentional baseline update;
  82 files cover local, production and offline metadata/manifest rendering.
  This is not an image build or an installed offline bundle.
- Full platform suite: not green. The first invocation also had a PATH issue
  in shell subprocesses; rerun with the tooling virtualenv first on PATH.
  The corrected invocation ran 849 tests: 68 failures and 65 errors. This
  total includes environment failures (`/tmp` is unavailable to mktemp),
  not just app-integration failures. Independent reproductions confirm:
  - `tests.test_app_ops_failover`: 11 tests, 8 errors, because fixture
    hostnames omit guestbook (`KeyError: 'guestbook'`).
  - `test_install.InstallTests.test_every_pod_dev_order`: fails because
    `deploy/installer/tests/fake_host.py` always answers 200 for checks,
    while /api/entries correctly expects 401.
- No image build, live PostgreSQL/Keycloak/browser exercise or lab run was
  possible locally (no Podman). Those remain unverified.

The user requested a review PR while these failures were being investigated.
This is a draft for another agent, not a completed phase 5-4 acceptance.
The maintainer should separate tests with fixed app fixtures from tests of
all configured apps, fix the status-code mocks, and add Guestbook's real
browser and DR coverage. No files under deploy/, proxy/ or keycloak/ changed.
