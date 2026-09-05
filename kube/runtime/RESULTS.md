# Grouped Podman Kube runtime results

## Static gate — passed

The canonical manifests define three workload boundaries: `todo-app`
(`todo-migrate`, `todo-backend` and `todo-frontend`), `todo-keycloak`, and
`todo-postgres`. The `.kube` units use `--no-pod-prefix`, so those explicit
container names are also the stable Podman names.
Static YAML and regression tests pass locally, including bounded connection
retry and immediate failure for authentication and SQL errors.

## Oracle Linux acceptance — passed on 688a0f6

Individual phases, including clean installation, idempotent redeploy, cold
reboot, persistence and replication, have passed in repaired lab runs.
Functional DR has also been demonstrated through promotion, application
recovery, isolated PITR, standby rebuild and sequential reboots, including
trusted Chromium tests and persistent markers. See the source repository's
PROJECT.md development journal for evidence and repairs.

The complete unchanged-revision acceptance passed on 2026-09-05 at
`688a0f67d190cd48dc6a8e4cfbedba66a89a5e24`, including real Keycloak Chromium
tests with TLS verification. See the [run record](../../docs/ACCEPTANCE-688a0f6.md).
This verdict applies to that revision, not automatically to later changes.
Legacy retirement is separate; no legacy files were removed during the test.

Historical results
for the superseded four-pod application shape are retained in
[`RESULTS-FOUR-POD-HISTORICAL.md`](RESULTS-FOUR-POD-HISTORICAL.md);
they do not approve the grouped model.
