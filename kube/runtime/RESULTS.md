# Grouped Podman Kube runtime results

## Static gate — passed

The canonical manifests define three workload boundaries: `todo-app`
(migration init, backend and frontend), `todo-keycloak`, and `todo-postgres`.
Static YAML and regression tests pass locally, including bounded connection
retry and immediate failure for authentication and SQL errors.

## Oracle Linux acceptance — pending

The grouped model has **not yet** passed the Oracle Linux migration, cold
reboot, rollback, replication or full DR acceptance gates. Historical results
for the superseded four-pod application shape are retained in
[`RESULTS-FOUR-POD-HISTORICAL.md`](RESULTS-FOUR-POD-HISTORICAL.md);
they do not approve the grouped model.
