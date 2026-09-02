# Two-node Kube replication results

## Static safety gate — passed

Manifest parsing and regression tests cover isolated names, external secrets,
least-privilege replication, bounded slot WAL, partial-bootstrap refusal,
read-only standby startup and explicit systemd failure propagation. The
embedded primary-init and standby shell programs also pass `sh -n`.

## Local primary runtime gate — passed

The primary manifest initialized a disposable database through direct
`podman kube play`. It became healthy and created only
`todo_kube_replicator`, with `REPLICATION` enabled and superuser, database
creation, role creation and inheritance disabled. The expected SCRAM HBA rule
and `max_slot_wal_keep_size=1GB` were active. All disposable local objects were
removed after the test.

## Oracle Linux two-node gate — passed

The isolated candidate was exercised on the two accepted Oracle Linux 9 hosts
with rootless Podman 5.8.2, SELinux enforcing and fapolicyd active. The
candidate primary ran on `192.168.0.108:15432`; firewalld allowed that port
only from the candidate standby host at `192.168.0.102`.

The runtime test proved:

- the two hosts used identical externally provisioned replication credentials;
- `todo_kube_replicator` had replication and login privileges, without
  superuser, database-creation, role-creation or inheritance privileges;
- the SCRAM replication HBA rule and `max_slot_wal_keep_size=1GB` were active;
- the physical slot `todo_kube_standby` was active with `wal_status=reserved`;
- the standby reported recovery mode, read-only transactions and an active WAL
  receiver using the named slot;
- committed marker rows streamed across the two hosts with asynchronous,
  zero-byte apply lag;
- restarting the standby `.kube` service recreated its pod, retained the same
  database cluster and resumed streaming;
- the restart-time init container detected `PG_VERSION` and logged
  `Existing PostgreSQL data found; base backup skipped.`; and
- the accepted application and accepted rebuilt standby remained healthy and
  unchanged throughout the isolated test.

The candidate standby was stopped and removed before the candidate primary.
Both candidate pods, generated units, secrets and persistent volumes were
removed. The temporary TCP 15432 firewalld rule was removed, the port was
released, and the shared accepted `todo-network` was retained. The accepted
application still returned `{"status":"ready"}` after cleanup.

This passes the isolated Kube replication gate. It validates the workload
mechanics only; it does not yet replace the accepted replication, promotion,
backup, PITR or standby-rebuild implementation.
