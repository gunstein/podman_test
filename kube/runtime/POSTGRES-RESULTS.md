# Canonical PostgreSQL Kube migration results

## Static gate — pending

The shared manifest, controlled primary migration, rollback, release package
and regression tests must pass locally before target deployment.

## Oracle Linux integrated gate — pending

The current-primary test must prove:

- stable `todo-postgres.service`, pod and container names;
- reuse of the accepted data and backup volumes;
- unchanged database system identifier and application data;
- writable primary state and continuous WAL archiving;
- resumed asynchronous replication with zero-byte lag;
- existing DR and backup tools through `podman exec todo-postgres`;
- application readiness, public HTTPS and browser E2E;
- reboot behavior; and
- exact rollback to the preserved per-container database Quadlet.

This gate migrates only the current primary. It does not authorize the rebuilt
standby, promotion, PITR or standby-reseed workflows to use Kube YAML yet.
