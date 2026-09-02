# Backend Kube candidate results

## Static checks — passed

The candidate is isolated from the accepted backend, publishes no host port,
uses external ConfigMap and secret inputs, runs as UID/GID 1000, drops all
capabilities, has an explicit memory limit and liveness probe, and combines
`ExitCodePropagation=any` with systemd `Restart=on-failure`.

## Oracle Linux application gate — passed

Tested on 2026-09-02 with rootless Podman 5.8.2, SELinux enforcing and
fapolicyd active, while the accepted Todo runtime remained online.

Observed results:

- the existing raw application password was translated in shell memory to a
  separate external Kube secret without a plaintext file;
- ConfigMap values and the secret file reached the backend correctly;
- the secret file was mode `0444`, and the backend ran as UID/GID 1000;
- the candidate resolved and connected to the accepted PostgreSQL service;
- liveness became healthy, `/ready` returned ready and a real API read
  returned all seven Todo rows;
- killing the backend caused systemd to remove and recreate the workload after
  the configured restart delay;
- the replacement container had a new ID and became healthy;
- no failed user units remained after recovery;
- the accepted backend and complete Todo application remained active and
  ready before, during and after the parallel test;
- cleanup removed the candidate unit, pod and translated secret while
  retaining the shared Todo network and all accepted services.

The brief failed healthcheck unit seen during initial startup cleared when the
next probe succeeded. Operational readiness remains an explicit endpoint
check; systemd `active` and liveness alone are not treated as readiness.
