# Grouped Podman Kube runtime results

## Static gate — passed

The canonical manifests define three workload boundaries: `todo-app`
(`todo-migrate`, `todo-backend` and `todo-frontend`), `todo-keycloak`, and
`todo-postgres`. The `.kube` units use `--no-pod-prefix`, so those explicit
container names are also the stable Podman names.
Static YAML and regression tests pass locally, including bounded connection
retry and immediate failure for authentication and SQL errors.

## Oracle Linux acceptance — pending

Individual phases, including clean installation, idempotent redeploy, cold
reboot, persistence and replication, have passed in repaired lab runs.
Functional DR has also been demonstrated through promotion, application
recovery, isolated PITR, standby rebuild and sequential reboots, including
trusted Chromium tests and persistent markers. See the source repository's
PROJECT.md development journal for evidence and repairs.

The complete **unchanged-revision Oracle Linux acceptance remains pending**.
Those repaired runs do not approve a final revision; the new frontend auth
adapter still needs real Keycloak integration verification in that run.
Follow the [acceptance checklist](../../docs/MANUAL-DR-QUICKSTART.md) before
marking this gate passed or retiring legacy transition tooling.

Historical results
for the superseded four-pod application shape are retained in
[`RESULTS-FOUR-POD-HISTORICAL.md`](RESULTS-FOUR-POD-HISTORICAL.md);
they do not approve the grouped model.
