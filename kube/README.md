# Podman Kube runtime

This directory evaluates Podman Kube YAML as a shared workload definition for
development and production. Clean install now uses this runtime directly. The
accepted historical per-container implementation remains available at the
`quadlet-reference-v1` tag while the final Kube runtime completes the same
Oracle Linux, SELinux, reboot, offline, backup and DR acceptance gates.

Podman Kube YAML is used here as a Podman workload format. This project does not
install a Kubernetes cluster and does not claim Kubernetes or OpenShift
portability.

## Current architecture

Start with the [canonical runtime](runtime/README.md). It defines the current
three-workload architecture:

```text
todo-app pod       migration init + backend + frontend
todo-keycloak pod  shared identity service
todo-postgres pod  shared persistent database
        |
        +---------- todo.network
```

The runtime guide identifies the small set of workload, ConfigMap, Quadlet and
network files that define this architecture. It also separates that core from
the Todo-specific backup, replication and disaster-recovery contracts. Current
status belongs in [runtime/RESULTS.md](runtime/RESULTS.md).

The final runtime remains unaccepted until it passes clean install, cold reboot,
replication, standby rebuild and full DR gates on Oracle Linux.

## Isolated evidence and history

These directories explain how individual contracts were established. They are
not the recommended reading order or the current deployment topology:

- [poc/](poc/README.md) proves the basic Podman Kube lifecycle;
- [backend/](backend/README.md), [nginx/](nginx/README.md),
  [keycloak/](keycloak/README.md) and [postgres/](postgres/README.md) preserve
  isolated component gates;
- [replication/](replication/README.md) proves the two-host physical replication
  contract used by the operational layer;
- [name-contract/](name-contract/README.md) proves the stable PostgreSQL name
  required by existing DR and backup commands; and
- [historical runtime results](runtime/RESULTS-FOUR-POD-HISTORICAL.md)
  retain evidence for superseded runtime shapes.

The controlled current-primary database migration is documented separately in
[POSTGRES-KUBE-MIGRATION.md](../ansible/POSTGRES-KUBE-MIGRATION.md).
