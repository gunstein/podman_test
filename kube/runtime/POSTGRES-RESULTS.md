# Canonical PostgreSQL Kube migration results

## Static gate — passed

The shared manifest, controlled primary migration, rollback, release package
and regression tests pass locally. The complete Python suite passed with 92
tests, all non-templated YAML parsed, both migration playbooks passed Ansible
syntax-check from the clean `cf226c3` operations package, and its checksum and
embedded source revision matched.

## Oracle Linux integrated gate — passed

The first controlled attempt reached the Kube service but PostgreSQL could not
read the existing data volume. The accepted `:Z` mount had left the volume at
the private label `container_file_t:s0:c112,c648`, while the new pod received a
different MCS label. PostgreSQL exited before initialization with permission
denied; both persistent volumes remained present. The dedicated rollback
restored the per-container Quadlet, the original database system, application,
zero-lag standby replication and healthy WAL archiving.

The corrected migration explicitly verifies the Podman-owned mountpoints and
relabels only the stopped data and backup volume trees to the shared
`container_file_t:s0` label before starting Kube. The rerun completed on
Oracle Linux 9 with rootless Podman 5.8.2.

The current-primary test proved:

- stable `todo-postgres.service`, pod and container names;
- reuse of the accepted data and backup volumes;
- unchanged database system identifier and application data;
- writable primary state and continuous WAL archiving;
- resumed asynchronous replication with zero-byte lag;
- existing DR and backup tools through `podman exec todo-postgres`;
- application readiness, public HTTPS and browser E2E;
- reboot behavior; and
- exact rollback to the preserved per-container database Quadlet during the
  failed first attempt.

The successful rerun retained database system identifier
`7680265831322587170`, the accepted data and backup volumes, the existing base
backup and all archived WAL. The stable service, pod and container are all
named `todo-postgres`; the container was healthy with
`HealthcheckOnFailureAction=kill`. The primary remained `f|off|on`, the rebuilt
standby remained `t|on`, asynchronous replication returned to zero-byte lag,
and WAL archiving was healthy with no failed attempts.

A deliberate `SIGKILL` recreated the complete Kube workload under systemd.
PostgreSQL performed crash recovery, retained the same system identifier and
resumed replication and archiving. Immediately after that unclean restart,
`pg_stat_archiver` reported no last archived WAL because its cumulative
statistics had reset. The backup volume was still mounted and contained the
existing base backup and 60 WAL files. After the next archive operation,
`cluster-status.yml` passed with WAL `00000002000000000000003B` and later
`00000002000000000000003D`, zero archive failures and zero replication lag.

After a host reboot, the PostgreSQL Kube service was active and healthy under
the stable name with the same database identity. Backend, Keycloak and nginx
were then migrated as well. A second reboot brought up all four Kube pods;
all containers became healthy, the nginx TLS root retained SHA-256
`a9b4ec01d39da1e5d1ef698308faf357655444867013c28c702da01a3f8a9e13`,
public HTTPS and both browser E2E tests passed, and the final replication,
archive and backup status remained healthy.

This gate migrates only the current primary. It does not authorize the rebuilt
standby, promotion, PITR or standby-reseed workflows to use Kube YAML yet.
