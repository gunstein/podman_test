# Grouped Podman Kube runtime results

## Static gate — passed

The canonical manifests define three workload boundaries: `todo-app`
(`todo-migrate`, `todo-backend` and `todo-frontend`), `todo-keycloak`, and
`todo-postgres`. The `.kube` units use `--no-pod-prefix`, so those explicit
container names are also the stable Podman names.
Static YAML and regression tests pass locally, including bounded connection
retry and immediate failure for authentication and SQL errors.

## Oracle Linux acceptance — pending

The grouped model has **not yet** passed its clean Oracle Linux installation,
idempotent redeploy, cold reboot, persistence, replication or full DR
acceptance gates. Historical results
for the superseded four-pod application shape are retained in
[`RESULTS-FOUR-POD-HISTORICAL.md`](RESULTS-FOUR-POD-HISTORICAL.md);
they do not approve the grouped model.
