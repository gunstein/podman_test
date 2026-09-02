# PostgreSQL Kube persistence candidate results

## Static checks — passed

The candidate uses the pinned PostgreSQL image, an external non-secret
ConfigMap, an external Kube-compatible secret, an isolated PVC with explicit
UID/GID ownership, a loopback-only test port, non-root execution and an exec
liveness probe using `pg_isready` from the image.

The same manifest also passed a local direct `podman kube play` smoke test.
Fresh initialization, liveness, the `999:999` process identity, data-directory
ownership and external secret mount were verified before all local test objects
were removed.

## Oracle Linux persistence gate — passed

Tested on the accepted Oracle Linux 9 host with rootless Podman 5.8.2, SELinux
enforcing and fapolicyd active. The candidate ran beside the accepted database
with its own pod, `127.0.0.1:15432` listener, secret and data volume.

Verified behavior:

- PostgreSQL 17.11 initialized a fresh `todo` database and became healthy;
- authentication over `todo-network` succeeded with the existing raw Podman
  secret and failed with an intentionally wrong password;
- host authentication in `pg_hba.conf` used `scram-sha-256`;
- PostgreSQL ran as `999:999`, the data directory was `0700`, `PG_VERSION` was
  `0600`, and the read-only secret was `0444`;
- the PVC annotation produced `999:999` ownership in the Podman user namespace;
- the data volume and files had the SELinux type `container_file_t`;
- killing the PostgreSQL container caused the `.kube` service to recreate the
  pod and return it to healthy state;
- recovery logged an interrupted database, WAL redo and readiness;
- the PostgreSQL system identifier and committed marker row were unchanged
  after recreation;
- normal systemd stop performed a fast shutdown and complete checkpoint;
- normal Kube teardown removed the pod and released port 15432 while retaining
  the PVC; and
- the accepted PostgreSQL, backend, Keycloak and nginx services stayed active
  and the application remained ready throughout the test.

Cleanup explicitly removed the isolated PVC and translated secret. No
candidate unit, pod, volume, secret or port remained, and the accepted runtime
had no failed user units.

## Remaining database gates

Streaming replication, promotion, WAL archiving, PITR and standby rebuild are
deliberately outside this isolated persistence gate and remain required before
the Kube implementation can replace the accepted runtime.
