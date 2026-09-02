# Podman Compose evaluation

This branch evaluates Podman Compose without changing the accepted Quadlet
reference. The rollback points are:

- `quadlet-runtime-accepted`: exact runtime revision used by the complete
  nginx acceptance drill;
- `quadlet-reference-v1`: the documented Quadlet reference implementation.

## Provider boundary

`podman compose` is a wrapper around an external provider. It may select
`docker-compose` when several providers are installed. Every project command
must therefore set:

```text
PODMAN_COMPOSE_PROVIDER=/usr/bin/podman-compose
```

The initial feasibility target is the provider available on the Oracle Linux
lab hosts. Its exact version must be recorded by preflight and package
acceptance. A newer Python package is not silently installed into a user home:
that would add an offline dependency and another fapolicyd trust boundary.

## Feasibility gates

The migration may continue only when the selected provider demonstrates:

1. native external Podman secrets without plaintext secret files;
2. rootless named-volume ownership and SELinux-compatible mounts;
3. container healthchecks;
4. deterministic service selection for database-only and full-stack modes;
5. one user systemd service for boot and stop of the complete stack;
6. reboot behavior on Oracle Linux with lingering enabled;
7. the existing promotion, backup, PITR and re-seed safety boundaries.

Provider 1.0.6 parses service dependencies for creation order, but does not
implement Compose profiles or health-conditioned `depends_on` semantics.
Ansible must therefore continue to run database setup, migration and grants
explicitly and wait for health at destructive or security-sensitive
boundaries. Compose is the application model; it is not the DR controller.

The local probe also confirmed two provider-specific edges:

- external secret modes must be quoted, for example `mode: "0440"`;
  otherwise 1.0.6 passes decimal `288` to Podman;
- `up` may continue after an underlying `podman run` failure, so deployment
  automation must verify the expected containers and health rather than trust
  the provider exit status alone;
- `down` may leave the project network after an early create failure, so
  bounded cleanup must verify all project-owned resources.

## Local probe

The probe uses isolated `todo-compose-probe-*` resources and removes them on
exit:

```bash
scripts/test-compose-provider.sh
```

It explicitly selects `/usr/bin/podman-compose`, creates a temporary native
Podman secret and named volume, starts a non-root container, checks secret
ownership and mode, waits for the healthcheck, and verifies Compose ownership.
It does not touch the Todo application stack or either lab VM.

On an enforcing Oracle Linux host, the test harness can be piped to trusted
`/usr/bin/bash` while the Compose file remains ordinary data:

```bash
TODO_COMPOSE_REPOSITORY_DIRECTORY=$HOME/todo-compose-probe-src \
  bash -s < scripts/test-compose-provider.sh
```

This avoids adding a temporary test script to the fapolicyd trust database.
