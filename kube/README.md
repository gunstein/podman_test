# Podman Kube candidate

This directory evaluates Podman Kube YAML as a shared workload definition for
development and production. It is deliberately isolated from the accepted
per-container Quadlet runtime.

The accepted implementation remains available at the `quadlet-reference-v1`
tag. Nothing under this directory is part of the supported deployment until it
has passed the same Oracle Linux, SELinux, reboot, offline, backup and disaster
recovery acceptance tests.

Podman Kube YAML is used here as a Podman workload format. This project does not
install a Kubernetes cluster and does not claim Kubernetes or OpenShift
portability.

Start with [poc/README.md](poc/README.md).

After the lifecycle PoC has passed, the first isolated application migration
is [backend/README.md](backend/README.md). It exercises the real database and
secret contract in parallel with the accepted backend.

The next isolated workload is [nginx/README.md](nginx/README.md). It verifies
the frontend, reverse-proxy routes and persistent TLS state on alternate
loopback ports.

The third isolated workload is [keycloak/README.md](keycloak/README.md). It
joins the accepted identity service temporarily as a second `jdbc-ping`
cluster node and verifies environment-secret delivery and graceful shutdown.

The fourth isolated workload is [postgres/README.md](postgres/README.md). It
uses a separate database, secret, loopback port and persistent volume to prove
fresh initialization, rootless storage and crash recovery without touching the
accepted database or DR state.
