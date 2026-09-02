# Keycloak Kube candidate results

## Static checks — passed

The candidate uses the pinned Keycloak image, external non-secret ConfigMap,
external Kube-compatible secret, explicit `jdbc-ping`, a stable cluster node
name, non-root execution, a Bash-only liveness check and explicit readiness.

## Oracle Linux identity and clustering gate — passed

Tested on the accepted Oracle Linux 9 host with rootless Podman 5.8.2, SELinux
enforcing and fapolicyd active. The candidate ran beside the accepted Keycloak
workload on the existing `todo-network` and database.

Verified behavior:

- the candidate started in production mode from `localhost/todo-keycloak:m12`;
- the database and bootstrap administrator secrets reached Keycloak through
  environment variables without their values being printed or written to a
  plaintext file;
- the container retained the image identity `1000:0` under rootless Podman;
- management health returned `UP`, and the liveness check became healthy;
- realm discovery returned the stable issuer
  `https://todo.test:8443/auth/realms/todo`;
- `JDBC_PING` discovered the accepted node and formed a two-member Infinispan
  cluster;
- an explicit systemd restart recreated the workload, which became healthy and
  rejoined the same two-member cluster;
- systemd stop produced `ISPN000080` and `Keycloak stopped in 1.100s`;
- the accepted node then reported a one-member cluster view; and
- the accepted PostgreSQL, backend, Keycloak and nginx services stayed active
  and the application remained ready throughout the isolated test.

Cleanup removed the candidate pod, generated unit and translated Podman
secret. The accepted runtime had no failed user units afterwards.

## Finding: the HTTP relative path also scopes management health

The first liveness probe requested `/health/live` and stayed unhealthy even
though Keycloak was running and had joined the cluster. With
`KC_HTTP_RELATIVE_PATH=/auth`, the management health endpoint is
`/auth/health/live`; the unprefixed path returns 404. The final probe uses a
Bash TCP request to the prefixed path because the official Keycloak image does
not include curl or wget.
