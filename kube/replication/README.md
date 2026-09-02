# Isolated two-node Kube replication gate

This gate runs a separate PostgreSQL primary on `192.168.0.108` and a separate
physical standby on `192.168.0.102`. It does not use the accepted
`todo-postgres-data` volumes, containers, ports or replication slot.

The primary is published on TCP 15432. The standby uses an idempotent Podman
Kube init container to take one streamed base backup and create the physical
slot `todo_kube_standby`. On later pod recreation the init container sees
`PG_VERSION` and skips the destructive bootstrap step. A non-empty directory
without `PG_VERSION` is treated as a partial bootstrap and is rejected.

Both hosts construct external Kube-compatible secrets from their existing raw
Podman secrets. No secret value is stored in these manifests. The firewall must
temporarily allow only `192.168.0.102/32` to reach the candidate primary port.

This gate must prove:

- the primary replication role is least privilege;
- the physical slot is active and bounded by `max_slot_wal_keep_size=1GB`;
- the standby is read-only and streaming across the two hosts;
- committed data reaches the standby;
- standby pod recreation skips base backup and resumes streaming; and
- all candidate state and the temporary firewall rule can be removed without
  affecting the accepted cluster.

The completed target evidence is recorded in [RESULTS.md](RESULTS.md). Do not
use these candidate manifests against production data. Passing this isolated
gate permits work on the real deployment migration; it does not by itself
approve replacing the accepted replication or disaster-recovery workflows.
