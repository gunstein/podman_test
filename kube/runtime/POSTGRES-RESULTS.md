# Canonical PostgreSQL Kube migration results

## Static gate — pending

The shared manifest, controlled primary migration, rollback, release package
and regression tests must pass locally before target deployment.

## Oracle Linux integrated gate — pending

The first controlled attempt reached the Kube service but PostgreSQL could not
read the existing data volume. The accepted `:Z` mount had left the volume at
the private label `container_file_t:s0:c112,c648`, while the new pod received a
different MCS label. PostgreSQL exited before initialization with permission
denied; both persistent volumes remained present. The dedicated rollback
restored the per-container Quadlet, the original database system, application,
zero-lag standby replication and healthy WAL archiving.

The corrected migration explicitly verifies the Podman-owned mountpoints and
relabels only the stopped data and backup volume trees to the shared
`container_file_t:s0` label before starting Kube. The rerun remains pending.

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
