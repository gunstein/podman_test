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
