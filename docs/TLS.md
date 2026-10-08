# TLS and reverse proxy model

The separate shared proxy image (`localhost/todo-proxy:m12`) terminates TLS
in container `nginx`, owned by `shared-proxy.service`. It routes to the HTTP-only
frontends, FastAPI backends and Keycloak over Podman DNS. Frontend holds no TLS material.

Reverse-proxy choice and certificate authority choice are separate decisions.
nginx never acts as a CA in provided mode.

nginx has two TLS modes, chosen per host and stored in the TLS volume itself
(`tls-mode`): **local**, the default, where the pod makes its own demo CA (below),
and **provided**, where a separate CA process, which may run on the same host,
issues the certificate and nginx only uses it
([provided mode](#provided-mode-a-separate-ca-process)).
A single host uses the installer's commands below; a DR pair uses app-ops,
which gives both hosts their certificate before a failover needs it
([DR README](../deploy/dr/README.md#nginx-certificates-from-your-ca)).

## Current offline lab mode (local)

The pod's init container, `nginx-tls`, uses the image's OpenSSL package on
first start to create:

- a local demo CA;
- one server key and SAN certificate covering `todo.test` and `notes.test`
  (`APP_TLS_HOSTNAMES`, derived from the App registry); and
- a public `ca.crt` that an operator may explicitly install on a test client.

The files persist in the host-local `todo-nginx-data` Podman volume. The CA
private key and server private key never need to leave that volume. A hostname
addition causes a new leaf certificate from the same local CA. Both nginx
server blocks use that same leaf and key; there are no separate per-app CAs. A pod restart
renews an expiring leaf certificate (the init container runs at every start). Missing CA state causes a completely new
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

## Provided mode: a separate CA process

**Provided** means that nginx's certificate comes from a CA process outside
nginx's security domain; nginx only uses it. It does not mean a CA on another
machine. In the first version that CA runs on the same host as Podman and
nginx, as `deploy/scripts/app_ca.py`, with its own storage:

```text
                     one host
  CA storage (not Podman)            nginx TLS volume (todo-nginx-data)
  ├── ca.key  (encrypted)            ├── server.key   active key
  ├── ca.crt                         ├── server.crt   active certificate (+ chain)
  └── issued.log                     ├── ca.crt       the root clients trust
          ▲                          ├── request.key  pending key (renewal)
          │ CSR in,                  ├── request.csr  pending request
          │ certificate out          └── tls-mode     provided
          │                                 │
  app_ca.py sign  ◄── request.csr ──  tls-request    (writes, throwaway container)
  (or sudo todo-ca-sign in v1)  ──►   tls-install    (checks, then switches)
                                            │
                                     nginx-tls init container  (RW: checks at start)
                                     nginx                     (RO: serves)
```

Two private keys, two homes, never mixed:

| Key | Lives in | Made by | Used by | Leaves its home |
|---|---|---|---|---|
| `ca.key` | CA storage (`/var/lib/todo-ca` in v1) | `app_ca.py init` | `app_ca.py` signing only | never: not a volume, never mounted, never in the TLS volume |
| `server.key` | the TLS volume | `tls-request`, in a throwaway container | nginx | never: the CA receives only the CSR |

### Commands

```bash
# Once: the CA (development: a directory of the Podman user; v1: see below).
python3 deploy/scripts/app_ca.py init --directory ~/.local/share/todo-ca \
  --domain todo.example.org --domain notes.example.org

# For each certificate (first time and every renewal):
cd ~/todo-offline-m12
PYTHONPATH=deploy/installer python3 -m app_installer tls-request --output ~/host.csr
python3 deploy/scripts/app_ca.py sign --directory ~/.local/share/todo-ca \
  --request ~/host.csr --output ~/host.crt
PYTHONPATH=deploy/installer python3 -m app_installer tls-install \
  --certificate ~/host.crt --ca ~/.local/share/todo-ca/ca.crt
```

openssl asks for the CA key's passphrase. `--passphrase-file FILE` reads it
from a file instead, which must not be inside the CA directory (refused):
next to `ca.key` it would protect nothing.

- **`init`** makes `ca.key` (RSA 3072, encrypted with AES-256), `ca.crt` (20
  years, `CA:TRUE, pathlen:0`, X.509 name constraints for the given domains)
  and an empty `issued.log`: directory 0700, `ca.key` and `issued.log` 0600,
  `ca.crt` 0644. It refuses a directory that already holds a CA.
- **`sign`** refuses, before the CA key is used: a CA directory or key that
  others can read or that another user owns, a passphrase file inside it, a
  CSR whose own signature does not verify, any name that is not a plain DNS
  name inside the CA's domains (no wildcards, no IP addresses, at most 20
  names), a key below RSA 2048 or EC 256 bits, and more than 825 days. The
  subject (`CN=` the first name) and every extension (server use only,
  `CA:FALSE`) are set by the CA; nothing but the public key and the DNS
  names comes from the CSR. Every certificate is appended to `issued.log`
  with its serial, expiry, names and the signing uid. Even a certificate
  signed around the tool fails for names outside the domains: clients
  enforce the name constraints.
- **`tls-request`** makes a new key in the TLS volume and a CSR for every
  public hostname nginx serves; only the CSR leaves the volume.
- **`tls-install`** checks everything before the volume changes: the CA file
  is a self-signed root, the certificate chains to it (intermediates may
  follow it in the same file), is valid now for TLS servers, names every
  hostname, and belongs to the waiting key or the active one. Then it
  switches the volume to provided mode, removes the demo CA, reloads nginx
  (no restart) and waits until nginx serves the new certificate for every
  hostname. Every openssl step runs in a throwaway container of the proxy
  image with no network, so the host needs no openssl for this part.

From then on nothing issues a certificate in the pod: a missing file, a key
that does not match, or a hostname the certificate does not name stops the
pod with an `ERROR:` line in `journalctl --user -u shared-proxy.service`. It
never falls back to the demo CA. An expired certificate still starts, with a
warning, so browsers can name the cause. A new public hostname therefore
needs a new certificate first: `tls-request`, sign, `tls-install`, then
install with the new hostname.

### Renewal

```text
active:  server.key + server.crt     nginx keeps serving these
pending: request.key + request.csr   until the signed certificate passed every check
```

1. `tls-request` makes a new `request.key` and `request.csr`; the active pair
   is untouched. Every night the single host's backup run (`todo-backup.timer`)
   does this by itself 60 days before the certificate ends and copies the CSR
   to `~/.config/todo/nginx-tls-request.csr`; below 30 days the run fails, so
   it shows in `systemctl --user --failed`.
2. The CA signs the CSR (`app_ca.py sign`, or `sudo todo-ca-sign` in v1).
3. `tls-install` checks the certificate against the pending key. A
   certificate that fits neither key, misses a hostname or chains to another
   CA changes nothing, and the pending key keeps waiting.
4. Only then `request.key` becomes `server.key` and the new certificate
   `server.crt`, nginx reloads, and `tls-install` waits until it serves the
   new fingerprint for every hostname.

nginx reads the pair only when it starts or reloads; the reload comes after
the switch, and a start in between fails closed (the init container finds a
key that does not fit) and is restarted by systemd. `tls-status` shows the
mode and the days left at any time.

The CA itself is not renewed automatically: issue a new CA well before its 20
years end, and give clients the new `ca.crt` before switching hosts to it.
Going back to local mode is deliberate, not a fallback: `uninstall
--remove-data` removes the TLS volume, and the next install starts a new demo
CA.

### Two security levels

**Development and the first implementation: one Unix user.** The user that
runs rootless Podman also owns the CA directory and runs `app_ca.py`. Simple,
and the two keys still never mix (the CA key is encrypted, outside every
volume, never mounted), but weaker: anyone who becomes that user, through
Podman or a container escape to it, can read `ca.key` and try its passphrase,
or wait for the operator to type it.

**v1: root owns the CA.** Before v1 the CA moves to root:

```text
Podman user                         root
├── rootless Podman                 └── /var/lib/todo-ca        root:root 0700
├── the TLS volume                      ├── ca.key              root:root 0600
├── cannot read ca.key                  ├── ca.crt              root:root 0644
└── may run only:                       └── issued.log          root:root 0600
    sudo todo-ca-sign < host.csr > host.crt
```

`deploy/scripts/todo-ca-sign` is the whole interface: a CSR on stdin, the
certificate on stdout, no arguments. It runs `app_ca.py sign-stdin` with a
fixed directory (`/var/lib/todo-ca`), a fixed optional passphrase file
(`/etc/todo-ca/passphrase`, root 0400, outside the CA directory; without it
openssl asks on the terminal) and the fixed 365-day validity. The caller
chooses no CA key, no openssl command, no config file, no extension and no
output path, so it cannot overwrite a root file or sign anything the CA's
checks refuse. Setup, as root:

```bash
install -d -m 0755 /usr/local/lib/todo-ca
install -m 0644 deploy/scripts/app_ca.py /usr/local/lib/todo-ca/app_ca.py
install -m 0755 deploy/scripts/todo-ca-sign /usr/local/sbin/todo-ca-sign
python3 /usr/local/lib/todo-ca/app_ca.py init --directory /var/lib/todo-ca \
  --domain todo.example.org --domain notes.example.org
# sudoers (visudo -f /etc/sudoers.d/todo-ca): "" allows no arguments at all.
#   podman ALL=(root) NOPASSWD: /usr/local/sbin/todo-ca-sign ""
```

Then the Podman user signs with
`sudo todo-ca-sign < ~/host.csr > ~/host.crt` and installs with
`--ca /var/lib/todo-ca/ca.crt`, or a copy of it (the certificate is public).
Each signing is in `issued.log` with the uid that ran it.

**The limit of one host.** Full root compromise of the host means the CA must
be assumed compromised: root reads `ca.key` and can watch the passphrase
being used. That is the accepted price of a CA on the same machine as the
workload; the name constraints still limit what a stolen CA key can sign
for. In v1, a compromise of nginx, of a container or of the Podman user alone
does not give `ca.key`: the user can only ask `todo-ca-sign` for
certificates the CA's policy allows, and each one is logged. A CA on a
separate machine, or an organisational PKI, removes that limit; provided
mode works with either unchanged, as only the signing step differs.

### Why a TLS volume, not a Podman secret

nginx's key is made on the host, persists across restarts, is replaced at
each renewal while the old one keeps serving, and must be switched without
recreating the container. A volume fits that lifecycle; a Podman secret fits
credentials delivered from outside that do not change in place. So each
secret stays where its lifecycle puts it:

| What | Where |
|---|---|
| database and application credentials | Podman secrets |
| a signing certificate delivered as a file (for example `signing-cert.pfx`) | Podman secret / file secret |
| nginx's locally made `server.key` | the TLS volume |
| the CA's `ca.key` | CA storage, outside Podman |

### nginx reads the volume read-only

The shared-proxy pod has an init container, `nginx-tls` (the same image,
`TODO_TLS_ROLE=provision`), which mounts the TLS volume read-write. On every
pod start it issues or renews the demo certificate in local mode, or checks
the provided one, then exits. nginx itself runs with `TODO_TLS_ROLE=serve`
and the volume mounted read-only: it only checks that the files it is about
to serve fit, and never writes them. `tls-request` and `tls-install` write
through their own throwaway containers. So a compromised nginx can read its
own key but cannot replace it or the CA certificate beside it.

```text
tls-request, tls-install   RW   (throwaway containers, no network)
nginx-tls init container   RW   (local: issue/renew demo; provided: check)
nginx                      RO   (serve)
```

### With an organisational PKI

If your organisation has a CA (for example an offline root with an issuing
CA), use it instead of `app_ca.py`: give it the CSR from `tls-request` and
install what it returns, with its root as `--ca`. Each node still gets its
own key for the same service names, so a failover changes only the address
clients reach, not what they trust. Never reuse one node's key on another to
make distribution easier.

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
