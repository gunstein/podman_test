# nginx Kube candidate results

## Static checks — passed

The candidate uses the existing pinned frontend image, external ConfigMaps,
an explicitly owned persistent TLS volume, non-root UID/GID 101, isolated
loopback ports and the existing application network.

## Oracle Linux proxy and TLS gate — passed

Tested on 2026-09-02 with rootless Podman 5.8.2, SELinux enforcing and
fapolicyd active, while the accepted Todo runtime remained online.

Observed results:

- the external environment and nginx ConfigMaps were consumed successfully;
- the candidate bound only `127.0.0.1:18080` and `127.0.0.1:18443`;
- frontend content, health, readiness, Todo reads and Keycloak discovery were
  routed through the candidate;
- the generated certificate matched `todo.test`, and HTTPS verified against
  the candidate CA;
- Keycloak continued to advertise the stable issuer on port 8443 rather than
  the isolated candidate port;
- nginx ran as UID/GID 101, and both private keys were mode `0600`;
- the manifest-created volume appeared as subordinate host UID/GID 100100 and
  as UID/GID 101 inside `podman unshare` and the container;
- the volume and every TLS file had the `container_file_t` SELinux label;
- a systemd restart created a new container while preserving byte-identical CA
  and server certificate/key files;
- the CA exported before restart continued to validate HTTPS afterward;
- normal systemd stop removed the pod but retained the TLS volume;
- explicit cleanup removed the candidate volume and released both test ports,
  while the accepted services and shared network remained active and ready.

Direct host traversal of the mapped volume correctly failed because its
rootless ownership is outside the service user's host identity. Operational
inspection must use `podman unshare` or a container with the volume mounted;
loosening host permissions would defeat the ownership model being tested.
