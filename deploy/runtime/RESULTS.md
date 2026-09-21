# Podman Kube runtime validation status

The current architecture has four pods: `todo-app` (migration init, backend,
HTTP frontend), `todo-keycloak`, `todo-postgres`, and `shared-proxy` (container
`nginx`, persistent `todo-nginx-data` TLS volume).

The four-pod change requires its own full unchanged-revision VM acceptance.
Local tests and archive verification do not establish that verdict. Follow
[Acceptance](../../docs/ACCEPTANCE.md) from a clean selected revision and baseline.

Historical unchanged-revision acceptance passed on 688a0f6; see its
[record](../../docs/ACCEPTANCE-688a0f6.md). The subsequent simplification revision
12c3bef received a full CLEAN PASS on 7 September 2026. Neither verdict accepts
the newer shared-proxy architecture. Historical evidence remains in Git and
revision-specific records, separate from the normal execution procedure.
