# Grouped application redesign status

The canonical manifests now define three workload boundaries: `todo-app`
(migration init, backend and frontend), `todo-keycloak`, and `todo-postgres`.
Static YAML and regression tests pass locally, including bounded connection
retry and immediate failure for authentication and SQL errors. The grouped
model has **not yet** passed the Oracle Linux migration, reboot, rollback,
replication or full DR acceptance gates.

The results below are retained as historical evidence for the superseded
four-pod model. They do not approve the grouped model.

# Canonical Kube application runtime results

## Historical four-pod static integration gate — passed

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

## Historical four-pod Oracle Linux migration gate — passed

The controlled migration and rollback were exercised on Oracle Linux 9 with
rootless Podman 5.8.2. The current writable primary was the physical
`todo-standby` host; the physical `todo-primary` host remained the accepted
rebuilt standby throughout the test.

The migration changed only the application tier:

- `todo-postgres.service` retained its `.container` source, running container
  and database system identifier `7680265831322587170`;
- `todo-backend.service`, `todo-keycloak.service` and
  `todo-frontend.service` moved to the canonical `.kube` sources;
- the three independent Kube pods and their health checks were healthy;
- the original raw secrets remained present while the two Kube-compatible
  derived secrets were delivered without plaintext files;
- the nginx root certificate retained SHA-256
  `a9b4ec01d39da1e5d1ef698308faf357655444867013c28c702da01a3f8a9e13`;
- local health, readiness, Todo data and the stable Keycloak issuer passed;
- public HTTPS and both browser E2E tests passed before and after reboot; and
- replication stayed `streaming|async` with zero-byte lag, the rebuilt standby
  stayed read-only, and continuous WAL archiving remained healthy.

After reboot, all four canonical services were active and the three Kube pods
were running. PostgreSQL kept the same database system identifier and nginx
kept the same TLS identity. An early Keycloak health probe briefly produced a
failed transient healthcheck unit while Keycloak was starting; the next
automatic probe reported healthy and systemd removed the failed state without
manual intervention.

The dedicated rollback then restored the exact preserved per-container
application Quadlets. It removed the Kube runtime directory, pods and derived
secrets while keeping the PostgreSQL container, database system identifier,
nginx TLS identity, raw secrets and application data unchanged. All restored
services, health endpoints and the stable issuer passed.

The application-tier migration, reboot and rollback gate is therefore passed.
This result does not yet authorize PostgreSQL, backup, PITR or disaster-recovery
migration to Kube YAML; those operational contracts remain a separate gate.

## Historical four-pod full-stack gate — passed

After the corrected PostgreSQL migration passed independently, the application
migration was repeated on top of the canonical PostgreSQL Kube service. The
resulting production runtime used four independent Kube pods and the existing
stable user-systemd service names:

```text
todo-postgres.service
todo-backend.service
todo-keycloak.service
todo-frontend.service
```

The application migration did not recreate PostgreSQL. Its container identity
and database system identifier `7680265831322587170` were unchanged, and nginx
retained the same TLS root. Local routes, Todo data, the stable Keycloak issuer,
public HTTPS and both browser E2E tests passed. A full host reboot restored all
four services and all four pods; every container became healthy and no failed
user units remained after the normal Keycloak startup probe transition.

The final cross-host check reported the rebuilt standby `t|on`, asynchronous
streaming with zero-byte lag, writable primary state `f|off|on`, WAL
`00000002000000000000003D`, and zero archive failures. Full-stack startup and
application behavior are therefore validated.

A subsequent fresh physical backup and isolated named-point PITR drill passed
without changing the live Kube runtime. The restore was network-isolated,
contained only the before-target row, and exact cleanup preserved all live and
backup state. The full fencing, promotion and standby-rebuild sequence remains
the final separate acceptance gate before the Kube implementation can replace
the reference.
