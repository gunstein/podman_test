# Canonical Kube application runtime results

## Static integration gate — passed

The shared backend, Keycloak and frontend manifests parse as Podman-supported
Kube YAML. The development and rendered runtime ConfigMaps contain the same
four named objects. Regression tests enforce canonical pod and systemd names,
external secrets, the existing TLS volume, explicit restart ownership and the
PostgreSQL non-interference boundary.

The local Quadlet generator produced:

```text
todo-backend.service
todo-keycloak.service
todo-frontend.service
todo-network.service
```

All three Kube services resolve the shared `todo.network` and generated runtime
ConfigMap. Only `todo-frontend.service` is linked into `default.target`; its
dependencies pull in backend and Keycloak. Both Ansible playbooks pass
syntax-check, all embedded YAML parses, the operations package contains the
runtime, roles and runbook, and the complete local test suite passes.

## Oracle Linux integrated migration gate — pending

The controlled target test must still prove:

- the three application services move from `.container` to `.kube` sources;
- PostgreSQL keeps the same container, systemd unit and database system ID;
- the existing nginx TLS identity survives the workload replacement;
- backend readiness, Keycloak issuer, public HTTPS and browser E2E remain valid;
- all services and data survive a host reboot; and
- the dedicated rollback restores the exact saved per-container Quadlets.

This application-tier gate does not authorize PostgreSQL, replication, backup,
PITR or disaster-recovery migration.
