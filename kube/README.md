# Podman Kube runtime

Start with the [canonical runtime](runtime/README.md). Its three-workload architecture is:

- todo-app: migration init, backend and frontend/nginx;
- todo-keycloak: independent identity service;
- todo-postgres: independent persistent database.

The workloads share todo.network. This is Podman's supported Kube YAML subset,
not a Kubernetes cluster or a portability promise. Helm is the workload source
of truth; the runtime directory contains checked-in rendered manifests.

See [runtime results](runtime/RESULTS.md) for acceptance of revision 688a0f6.
Earlier component PoCs and transition tooling were retired after that gate.
They remain recoverable from pre-retirement Git history (c377161); the accepted
per-container reference remains at the immutable quadlet-reference-v1 tag.

Historical [RESULTS-FOUR-POD-HISTORICAL.md](../docs/history/RESULTS-FOUR-POD-HISTORICAL.md)
and [PostgreSQL evidence](../docs/history/POSTGRES-RESULTS.md) are not current runbooks.
