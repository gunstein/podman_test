# TLS and reverse proxy model

The separate shared proxy image (`localhost/todo-proxy:m12`) terminates TLS
in container `nginx`, owned by `shared-proxy.service`. It routes to the HTTP-only
frontends, FastAPI backends and Keycloak over Podman DNS. Frontend holds no TLS material.

Reverse-proxy choice and certificate authority choice are separate decisions.
nginx never acts as a CA in provided mode.

nginx has two TLS modes, chosen per host and stored in the TLS volume itself
(`tls-mode`): **local**, the default, where nginx makes its own demo CA (below),
and **provided**, where the organisation's own CA issues the certificate and
nginx only uses it ([provided mode](#provided-mode-the-organisations-own-ca)).
A single host uses the installer's commands below; a DR pair uses app-ops,
which gives both hosts their certificate before a failover needs it
([DR README](../deploy/dr/README.md#nginx-certificates-from-your-ca)).

## Current offline lab mode (local)

The container entrypoint uses the image's OpenSSL package on first start to
create:

- a local demo CA;
- one server key and SAN certificate covering `todo.test` and `notes.test`
  (`APP_TLS_HOSTNAMES`, derived from the App registry); and
- a public `ca.crt` that an operator may explicitly install on a test client.

The files persist in the host-local `todo-nginx-data` Podman volume. The CA
private key and server private key never need to leave that volume. A hostname
addition causes a new leaf certificate from the same local CA. Both nginx
server blocks use that same leaf and key; there are no separate per-app CAs. A container restart
renews an expiring leaf certificate. Missing CA state causes a completely new
trust root.

Clean deployment and application recovery require `io.todo.proxy=nginx` on
`localhost/todo-proxy:m12`. Rebuild with `refresh_images=true` on a connected
controller, or explicitly load the proxy archive from a verified current offline
bundle before deployment. The frontend image has no proxy identity contract.

This mode is deliberately self-contained and works offline, but it is not the
recommended certificate lifecycle for multiple services or normal operations.
Installing its public root after promotion makes trust distribution part of the
lab failover time.

### Local hostnames and trust

For direct development, add this entry to the test client’s `/etc/hosts`:

```text
127.0.0.1 todo.test notes.test
```

For a VM or server, use its serving IP instead. Export only the public CA:

```bash
podman cp nginx:/var/lib/todo-tls/ca.crt ./todo-nginx-root.crt
curl --cacert ./todo-nginx-root.crt https://todo.test:8443/ready
curl --cacert ./todo-nginx-root.crt https://notes.test:8443/ready
```

Trust that CA in the browser as well. Both apps authenticate against the same
canonical issuer `https://todo.test:8443/auth/realms/todo`; `/auth/` is reachable
from either hostname. The new `e2e/test_multi_app.py` requires real CA trust
(`E2E_CA_FILE` plus the browser trust store) and never ignores TLS errors.
It checks both hostnames serve the same certificate and that login transfers
from Todo to Notes without another password prompt.

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

The Playwright Chromium build can use a certificate store different from the
Ubuntu system store. Consequently, a successful trusted `curl` request is the
lab's CA-trust assertion, while `deploy/scripts/dev/run-e2e.sh` uses
`E2E_IGNORE_HTTPS_ERRORS=true` only to exercise the browser application flows.
Do not interpret the Playwright setting as proof of certificate trust.

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
`todo.test` and `notes.test` in one SAN certificate, while each node has a different private key. Clients trust the
root before an incident. Failover then changes only the active service address;
it does not issue a certificate or modify client trust.

The root private key must remain offline. The issuing CA and its database,
serial state, policy, renewal process, revocation data, protected backup and
audit trail are separate security responsibilities. Do not copy either CA
private key to an application node.

The deployment tooling should consume already issued artifacts:

- install the public certificate/full chain as a normal reviewed file;
- deliver the node-specific private key through a controlled deployment process
  into a Podman secret; and
- recreate or reload nginx in a controlled rotation and verify HTTPS before
  retiring the previous certificate.

Node-specific TLS keys require a per-host provisioning process; never reuse one
leaf private key merely to make distribution easier.

## Provided mode: the organisation's own CA

For a host without a central PKI: one small offline CA, kept on encrypted
storage that is offline between uses, signs a certificate for each host about
once a year. Each host makes its own private key, which never leaves its TLS
volume; only a certificate signing request (CSR) goes to the CA, and only the
signed certificate comes back. Clients trust the CA once.

```text
offline root CA (deploy/scripts/app_ca.py, 20 years, name constraints:
     |           only names in your domains)
     +-- certificate for host A (1 year) -- key made on host A
     +-- certificate for host B (1 year) -- key made on host B
```

**Once, on the administrator's machine** (Python 3 and openssl; openssl asks
for the CA key's passphrase):

```bash
python3 deploy/scripts/app_ca.py init --directory /media/ca-usb/todo-ca   --domain todo.example.org --domain notes.example.org
```

`--domain` limits what the CA can ever sign (X.509 name constraints): a
stolen CA key cannot issue a certificate for anyone else's site. Give every
client `ca.crt` (never `ca.key`), and keep a second copy of the directory in
another place.

**On the host, after a normal install** (the hostnames come from the
installed `shared-proxy.yaml`):

```bash
cd ~/todo-offline-m12
PYTHONPATH=deploy/installer python3 -m app_installer tls-request --output ~/host.csr
```

**On the administrator's machine**, sign it (at most 825 days, the most Apple
clients accept; 365 by default). The tool refuses any name outside the CA's
domains, and appends every certificate it issues to `issued.log`:

```bash
python3 deploy/scripts/app_ca.py sign --directory /media/ca-usb/todo-ca   --request host.csr --output host.crt
```

**Back on the host**:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer tls-install   --certificate ~/host.crt --ca ~/ca.crt
```

`tls-install` changes nothing until the certificate passes every check: the
CA file is a self-signed root, the certificate chains to it (intermediates
may follow the certificate in the same file), is valid now for TLS servers,
names every public hostname nginx serves, and belongs to the key waiting in
the volume. Then it switches the volume to provided mode, removes the demo CA
and its key, reloads nginx (no restart) and waits until nginx serves the new
certificate for every hostname. Every openssl step runs in a throwaway
container of the proxy image with no network, so the host needs no openssl.

From then on nginx's entrypoint only checks what it was given and never
issues anything: a missing file, a key that does not match, or a hostname the
certificate does not name stops nginx with an `ERROR:` line in
`journalctl --user -u shared-proxy.service`. It never falls back to the demo
CA. An expired certificate still starts, with a warning, so browsers can name
the cause. A new public hostname therefore needs a new certificate first:
`tls-request`, sign, `tls-install`, then install with the new hostname.

**Renewal.** Every night the single host's backup run (`todo-backup.timer`)
also looks at the certificate. 60 days before it expires it makes a new key
and request in the volume and copies the request to
`~/.config/todo/nginx-tls-request.csr`; below 30 days the run fails, so it
shows in `systemctl --user --failed`. Sign that request and `tls-install` the
result, as above. `tls-status` shows the mode and the days left at any time.
The CA itself is not renewed automatically: issue a new CA well before its 20
years end, and give clients the new `ca.crt` before switching hosts to it.

Going back to local mode is deliberate, not a fallback: `uninstall
--remove-data` removes the TLS volume, and the next install starts a new demo
CA.

## Proxy contract

nginx preserves the original request URI and sends explicit forwarding
metadata to FastAPI and Keycloak:

- `Host`
- `X-Forwarded-Host`
- `X-Forwarded-Proto`
- `X-Forwarded-Port`
- `X-Forwarded-For`
- `X-Real-IP`

nginx also sets security headers (`deploy/manifests/shared-proxy.yaml.j2`):
HSTS and `X-Content-Type-Options` on every response, and on the apps' own pages
and API a strict Content-Security-Policy (only same-origin scripts, styles
and frames, no inline script; requests to the same origin and to Keycloak's
canonical origin, where Notes fetches its tokens), `frame-ancestors 'none'`,
`X-Frame-Options: DENY` and a Referrer-Policy. Keycloak's pages under `/auth/`
keep Keycloak's own Content-Security-Policy and Referrer-Policy.

Keycloak locks an account for a minute after 5 failed logins, doubling up to
15 minutes, and requires passwords of at least 12 characters that differ from
the user name and email (`REALM_SECURITY` in
`deploy/installer/app_installer/keycloak.py`). The installer applies these to
the `todo` realm on every install and failover, so an existing realm gets them
too.

Application recovery must verify that Keycloak discovery still reports the stable external issuer
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
