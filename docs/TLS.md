# TLS and reverse proxy model

The Todo frontend image uses nginx because the demo is also a migration rehearsal
for a larger nginx-based service. nginx serves the static frontend and proxies
only the documented backend and Keycloak routes. Rootless Podman publishes
unprivileged host ports 8080 and 8443.

Reverse-proxy choice and certificate authority choice are separate decisions.
nginx never acts as a CA.

## Current offline lab mode

The container entrypoint uses the image's OpenSSL package on first start to
create:

- a local demo CA;
- one server key and certificate for `TODO_TLS_HOSTNAME`; and
- a public `ca.crt` that an operator may explicitly install on a test client.

The files persist in the host-local `todo-nginx-data` Podman volume. The CA
private key and server private key never need to leave that volume. A hostname
change causes a new leaf certificate from the same local CA. A container restart
renews an expiring leaf certificate. Missing CA state causes a completely new
trust root.

Existing Caddy-tagged frontend images are rejected by M12 and M14. Rebuild with `refresh_images=true` on a connected controller, or explicitly load the frontend archive from a newly verified offline bundle before deployment.

This mode is deliberately self-contained and works offline, but it is not the
recommended certificate lifecycle for multiple services or normal operations.
Installing its public root after promotion makes trust distribution part of the
lab failover time.

### Replacing client trust

When a Debian or Ubuntu test client replaces a previously installed demo CA at
the same path, a normal `update-ca-certificates` run can report `0 added` and
leave old generated trust links in place. Remove the old path, install the nginx
root under a distinct filename, and rebuild the generated store:

```bash
sudo rm -f /usr/local/share/ca-certificates/todo-m14.crt
sudo cp todo-nginx-root.crt /usr/local/share/ca-certificates/todo-nginx-root.crt
sudo update-ca-certificates --fresh
openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt todo-nginx-root.crt
```

The final command must report `OK`. Do not use `curl -k`; it bypasses the trust
property this test is intended to verify.

## Recommended moderate-deployment mode

Use an organizational certificate source with this trust shape:

```text
offline root CA
      |
issuing CA
      |
      +-- certificate A + private key A -- node A
      +-- certificate B + private key B -- node B
```

Both leaf certificates contain the stable service DNS name, such as
`todo.test`, while each node has a different private key. Clients trust the
root before an incident. Failover then changes only the active service address;
it does not issue a certificate or modify client trust.

The root private key must remain offline. The issuing CA and its database,
serial state, policy, renewal process, revocation data, protected backup and
audit trail are separate security responsibilities. Do not copy either CA
private key to an application node.

Ansible should consume already issued artifacts:

- install the public certificate/full chain as a normal reviewed file;
- deliver the node-specific private key from Ansible Vault or another
  authoritative secret source into a Podman secret; and
- recreate or reload nginx in a controlled rotation and verify HTTPS before
  retiring the previous certificate.

The current common-secret playbook provisions identical values to all selected
hosts. Node-specific TLS keys require host-specific encrypted variables or a
per-host secret mapping; never reuse one leaf private key merely to fit the
common-secret interface.

## Proxy contract

nginx preserves the original request URI and sends explicit forwarding
metadata to FastAPI and Keycloak:

- `Host`
- `X-Forwarded-Host`
- `X-Forwarded-Proto`
- `X-Forwarded-Port`
- `X-Forwarded-For`
- `X-Real-IP`

M14 must verify that Keycloak discovery still reports the stable external issuer
`https://todo.test:8443/auth/realms/todo`. Health, readiness, public API,
login redirect, token validation and logout/redirect behavior belong in proxy
acceptance testing.

## SELinux and fapolicyd

The TLS volume uses a Podman-managed volume with container labeling and rootless
UID mapping. A host bind mount would additionally require a correct SELinux
label such as `:Z`; permissive Unix modes do not bypass SELinux.

OpenSSL and the certificate bootstrap script execute inside the OCI container,
so host `fapolicyd` does not evaluate that script. Host-side PKI automation is
different: project-owned scripts must receive exact trust or, for a larger
Oracle Linux deployment, be packaged as signed RPM content and installed
through DNF. Using an RPM-provided OpenSSL binary does not automatically trust
an untrusted host-side script that invokes it.

Never disable SELinux or fapolicyd to make certificate deployment work.
