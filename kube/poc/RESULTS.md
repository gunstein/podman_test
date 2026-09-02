# Podman Kube PoC results

## Local development host — passed

Tested on 2026-09-02 with:

- rootless Podman 4.9.3
- cgroup v2
- SELinux disabled on the development host
- `localhost/todo-backend:m12`

Observed results:

- separate server and consumer pods resolved the server by
  `todo-kube-poc-server` on the shared network;
- the consumer completed an HTTP request to the server;
- an externally created Podman secret containing a Kubernetes Secret document
  was mounted as `/run/secrets/todo-kube-poc/token` with mode `0444`;
- the non-root consumer wrote to `todo-kube-poc-data`;
- normal `podman kube play --down` retained the named volume;
- interrupting `podman kube play --wait` removed the foreground pod;
- the Quadlet generator resolved `Network=todo-kube-poc.network` to the
  explicitly named `todo-kube-poc-network`;
- both `.kube` units started and used the same YAML as direct development;
- killing the consumer process made its user systemd service fail with exit
  status 137 when `ExitCodePropagation=any` was configured;
- all temporary PoC resources and installed test units were removed afterward.

## Oracle Linux lifecycle gate — passed

Initial target preflight on 2026-09-02 confirmed:

- Oracle Linux with Podman 5.8.2;
- rootless Podman with cgroup v2 and the systemd cgroup manager;
- SELinux enforcing and fapolicyd active;
- the rootless user manager had `Delegate=yes`, but its `user.slice` exposed
  only the `memory` and `pids` controllers.

The first pod start correctly exposed a platform constraint: the initial
`resources.limits.cpu` setting failed in `crun` because the rootless hierarchy
did not expose the `cpu` controller. The PoC now retains its memory limit and
omits the optional CPU limit. No host cgroup configuration was weakened.

The second start proved network DNS, HTTP, external secret delivery, non-root
volume access and correct `container_file_t` labels. It also exposed two image
and probe details:

- Podman's `tcpSocket` liveness probe generated an `nc` command, but the slim
  Python image does not contain netcat;
- the image's `USER 1000` declaration selected UID 1000 with primary GID 0.

The revised manifests use the image's normal Uvicorn process, an exec probe
implemented by Python already present in the image, and explicit UID 1000 and
GID 1000. Using the normal application process also gives the PoC
representative SIGTERM handling.

The revised Uvicorn workload then passed its local liveness check and stopped
in approximately one second through normal `podman kube play --down`, without
the previous SIGKILL fallback.

The revised workload then passed the target Oracle Linux checks:

- separate pods resolved each other through the shared rootless network and
  communicated over HTTP;
- the Python liveness probe became healthy;
- the externally provisioned secret was mounted and readable without secret
  material in Git;
- UID and GID were both 1000 inside the consumer;
- the consumer wrote to the named volume while SELinux labelled its content
  `container_file_t`;
- the Uvicorn workload stopped cleanly through foreground `--wait` cleanup;
- independent `.kube` units ran the same YAML and shared network;
- killing the consumer propagated exit status 137 to its systemd service,
  while the independent server service stayed active;
- restarting the failed service restored the consumer with its secret and
  persistent data intact;
- after reboot, both `.kube` workloads were active, the server was healthy,
  and the external secret and persistent data were intact;
- `LogDriver=journald` was active after reboot, `podman logs` exposed the
  Uvicorn output to the rootless operator, and the same records were present
  in the system journal under the container name;
- the accepted Todo reference services remained active and ready throughout
  the PoC and after reboot.

This passes the PoC lifecycle and observability gate on the supported Oracle
Linux host. Direct `journalctl --user -u` queries did not expose container
output; the supported unprivileged operator interface is `podman logs`, while
direct system-journal inspection requires suitable journal privileges.
