# Accepted per-container Quadlet reference

This directory is the source for the accepted deployment and its rollback
boundary. It is not the target architecture. The grouped Podman Kube candidate
lives in [`kube/runtime/`](../kube/runtime/README.md).

The files currently have two roles:

- the seven `.container` files implement the accepted per-container runtime,
  including the one-shot database setup, migration and grant jobs;
- `todo.network`, `todo-postgres-data.volume` and
  `todo-nginx-data.volume` are shared resources also retained by the Kube
  migration.

`todo-migrate.container` is replaced by the migration init container in the
grouped `todo-app` pod. The other long-running `.container` files are replaced
by the three canonical `.kube` workloads. They remain here only because the
grouped candidate has not completed its acceptance and must still be able to
roll back to the proven implementation.

## Retirement gate

After the grouped model passes clean install, idempotent redeploy, cold reboot,
persistence, replication, promotion, standby rebuild and full DR acceptance:

1. Make the three-workload Kube model the default deployment and offline path.
2. Remove the seven legacy `.container` files from the main runtime path, or
   move them under an explicitly historical `quadlet-reference/` directory.
3. Move the shared network and volume definitions beside the canonical Kube
   runtime.
4. Move any still-required database setup and grant work to an explicit
   provisioning boundary rather than the application runtime.
5. Update Ansible, package builders, tests and documentation, then verify that
   no production path refers to the legacy files.
6. Remove the now-unused per-container templates retained in the standby,
   promotion, backup and rebuild roles once the full VM/DR gate proves those
   paths use only the canonical Kube workloads.

The immutable `quadlet-reference-v1` tag preserves the accepted implementation
even after this cleanup. Do not remove the files from the current branch before
the complete Kube VM/DR acceptance gates pass.
