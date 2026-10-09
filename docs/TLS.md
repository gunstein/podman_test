# TLS and reverse proxy model

The separate shared proxy image (`localhost/todo-proxy:m12`) terminates TLS
in container `nginx`, owned by `shared-proxy.service`. It routes to the HTTP-only
frontends, FastAPI backends and Keycloak over Podman DNS. Frontend holds no TLS material.

Reverse-proxy choice and certificate authority choice are separate decisions.
nginx never acts as a CA in provided mode.

nginx has two TLS modes, chosen per host and stored with its files
(`tls-mode`): **local**, the default, where the installer makes a demo CA
(below), and **provided**, where a separate CA process, which may run on the
same host, issues the certificate and nginx only uses it
([provided mode](#provided-mode-a-separate-ca-process)).

Those files are **Podman secrets** by default, mounted read-only into nginx
from a Kube secret ([below](#nginxs-tls-files-as-podman-secrets)). The earlier
**TLS volume** `todo-nginx-data` is kept, so a host can go back to it
([switching back](#switching-back-to-the-tls-volume)); the two modes and every
command are the same with either.
A single host uses the installer's commands below; a DR pair uses app-ops,
which gives both hosts their certificate before a failover needs it
([DR README](../deploy/dr/README.md#nginx-certificates-from-your-ca)).

## nginx's TLS files as Podman secrets

This is the worked example of a file delivered as a Podman secret: a private
key and certificates that a program (here nginx) reads as ordinary files.
The code is `deploy/installer/app_installer/tls_secrets.py`; the manifest is
`deploy/manifests/shared-proxy.yaml.j2`.

### One secret per file, one Kube secret for nginx

| File in nginx | Raw Podman secret | In nginx's Kube secret |
|---|---|---|
| `tls-mode` | `todo-proxy-tls-mode` (`local` or `provided`) | yes |
| `ca.crt` | `todo-proxy-ca-cert` | yes |
| `server.crt` | `todo-proxy-tls-cert` (certificate, then any intermediates) | yes |
| `server.key` | `todo-proxy-tls-key` | yes |
| `ca.key` | `todo-proxy-ca-key` (demo CA, local mode only) | **no** |
| `request.key` | `todo-proxy-tls-request-key` (waiting for its certificate) | **no** |

nginx's Kube secret, `todo-kube-proxy-tls-secret`, holds exactly the four
files nginx serves. The CA's key and a waiting key are never in it, so nginx
never sees them.

### Step 1: a file becomes a secret

`podman secret create NAME FILE` stores a file's content as a secret; `-`
reads it from stdin. The installer never writes a key to a file on the
host: openssl runs in a throwaway container and prints the key, and the
installer passes what it printed to `podman secret create`:

```bash
podman run --rm --network none --user 101:101 --tmpfs /work:mode=1777 \
  --entrypoint openssl localhost/todo-proxy:m12 genpkey -algorithm RSA \
  -pkeyopt rsa_keygen_bits:3072 \
  | podman secret create todo-proxy-tls-key -
```

An existing secret is replaced with `podman secret create --replace NAME -`.
Podman 4.9 refuses `--replace` for a name that does not exist yet, so the
installer passes it only when the secret exists.

### Step 2: a secret becomes a file in a container

`podman run --secret NAME,type=mount,target=PATH,uid=UID,gid=GID,mode=MODE`
mounts a secret as a read-only file at PATH. Every step that needs a key
gets it this way, for the nginx user only:

```bash
podman run --rm --network none --user 101:101 --tmpfs /work:mode=1777 --workdir /work \
  --secret todo-proxy-tls-key,type=mount,target=/run/todo-tls/server.key,uid=101,gid=101,mode=0400 \
  --entrypoint openssl localhost/todo-proxy:m12 \
  req -new -key /run/todo-tls/server.key -subj /CN=todo.test
```

### Step 3: nginx gets a directory of files from a Kube secret

`podman kube play` reads a Kubernetes `Secret` stored as a Podman secret: a
JSON (or YAML) document whose `data` holds base64 values. A `secret:` volume
then mounts it as a directory with one file per key:

```yaml
# The Podman secret todo-kube-proxy-tls-secret holds:
#   {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "todo-kube-proxy-tls-secret"},
#    "data": {"tls-mode": "...", "ca.crt": "...", "server.crt": "...", "server.key": "..."}}
  containers:
    - name: nginx
      volumeMounts:
        - name: tls-data
          mountPath: /var/lib/todo-tls
          readOnly: true
  volumes:
    - name: tls-data
      secret:
        secretName: todo-kube-proxy-tls-secret
        optional: false
        defaultMode: 0444
```

nginx reads `/var/lib/todo-tls/server.crt` and `server.key` as before. Podman
mounts the files as root, so they need mode 0444 for nginx (user 101); only
nginx runs in that container. To mount them, `podman kube play` (checked with
Podman 4.9) writes the files into a named volume called after the Kube
secret, `todo-kube-proxy-tls-secret`, and rewrites it at every play. That
volume stays after `kube down`, so the key is on disk there as well as in
the secret store, both under the same Podman user, while the stack is
installed; `uninstall` and the development `down` remove it with the pods,
as every Kube secret's volume ([Secrets](SECRETS.md#a-kube-secret-also-lives-in-a-volume)). The installer builds the JSON from the four raw
secrets (`publish()`) and replaces it when one of them changed.

### Step 4: a changed secret needs a new container

Podman copies a secret into a container when the container is created. A
running nginx keeps the files it started with: `nginx -s reload` and even
`podman restart` still see the old ones; only a new pod does
(`systemctl --user restart shared-proxy.service`, which runs
`podman kube play --replace`). So every change goes to the raw secrets
first, the Kube secret last, and then the installer restarts nginx. A new
certificate costs a few seconds without HTTPS, where the volume needed only a
reload.

### Inspecting the secrets

```bash
podman secret ls --filter name=todo-proxy                 # the raw secrets
podman secret ls --filter name=todo-kube-proxy-tls        # nginx's Kube secret
podman exec nginx ls -l /var/lib/todo-tls                 # the four files nginx sees
podman exec nginx cat /var/lib/todo-tls/ca.crt            # the public root for clients
```

Never print `podman secret inspect --showsecret` for a key: it shows the key.

## Current offline lab mode (local)

Before nginx starts, the installer (`install`, and `deploy-promoted` on a DR
host) uses the OpenSSL in the proxy image to create:

- a local demo CA (10 years);
- one server key and SAN certificate (397 days) covering `todo.test` and
  `notes.test` (the public hostnames, derived from the App registry); and
- a public `ca.crt` that an operator may explicitly install on a test client.

They are host-local Podman secrets. The demo CA's private key stays in its own
secret and never reaches nginx. A hostname addition causes a new leaf
certificate from the same local CA. Both nginx server blocks use that same
leaf and key; there are no separate per-app CAs. Every install replaces a
leaf within 30 days of its end, and so does
`python3 -m app_installer tls-renew`, which then restarts nginx; it touches
only nginx's secrets, so it also runs on a DR host. A pod restart alone
renews nothing. A missing CA, or one within 30 days of its end, causes a
completely new trust root.

A host that served from the TLS volume keeps its CA: the first install with
Podman secrets copies the volume's files into secrets (`adopt_volume()`), so
clients need nothing new. The volume itself stays until
`uninstall --remove-data`, which removes the TLS secrets too.

With the TLS volume instead, the pod's init container, `nginx-tls`, makes the
same files in `todo-nginx-data` at every pod start, so a pod restart renews an
expiring leaf certificate.

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
podman exec nginx cat /var/lib/todo-tls/ca.crt > ./todo-nginx-root.crt
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
  CA storage (not Podman)            nginx's Podman secrets (todo-proxy-*)
  ├── ca.key  (encrypted)            ├── server.key   active key
  ├── ca.crt                         ├── server.crt   active certificate (+ chain)
  └── issued.log                     ├── ca.crt       the root clients trust
          ▲                          ├── request.key  pending key (renewal)
          │ CSR in,                  └── tls-mode     provided
          │ certificate out                 │
          │                                 │
  app_ca.py sign  ◄── CSR ──────────  tls-request    (new key: throwaway container → secret)
  (or sudo todo-ca-sign in v1)  ──►   tls-install    (checks, then switches the secrets)
                                            │
                                     todo-kube-proxy-tls-secret  (the four served files)
                                            │
                                     nginx  (RO: serves; restarted to read new files)
```

With the TLS volume the same files are in `todo-nginx-data` (with the CSR as
`request.csr`), the `nginx-tls` init container checks them at every start,
and `tls-install` reloads nginx instead of restarting it.

Two private keys, two homes, never mixed:

| Key | Lives in | Made by | Used by | Leaves its home |
|---|---|---|---|---|
| `ca.key` | CA storage (`/var/lib/todo-ca` in v1) | `app_ca.py init` | `app_ca.py` signing only | never: not Podman, never mounted, never one of nginx's files |
| `server.key` | the Podman secret `todo-proxy-tls-key` (or the TLS volume) | `tls-request`, in a throwaway container | nginx | never: the CA receives only the CSR |

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
- **`tls-request`** makes a new waiting key (the secret
  `todo-proxy-tls-request-key`) and a CSR for every public hostname nginx
  serves, as every install records them (`~/.config/todo/target-values.json`);
  only the CSR leaves the host's secret store.
- **`tls-install`** checks everything before nginx's files change: the CA
  file is a self-signed root, the certificate chains to it (intermediates
  may follow it in the same file), is valid now for TLS servers, names every
  hostname, and belongs to the waiting key or the active one. The two
  certificate files are checked as temporary secrets
  (`todo-proxy-tls-incoming*`), removed afterwards. Then it switches the
  secrets to provided mode, removes the demo CA's key and the waiting key,
  replaces nginx's Kube secret, restarts a running nginx and waits until it
  serves the new certificate for every hostname. Every openssl step runs in
  a throwaway container of the proxy image with no network, so the host needs
  no openssl for this part. With the TLS volume, `tls-install` switches the
  volume and reloads nginx instead (no restart).

From then on nothing issues a certificate: a missing file, a key that does
not match, or a hostname the certificate does not name stops the pod (and an
install before it) with an `ERROR:` line in
`journalctl --user -u shared-proxy.service`. It
never falls back to the demo CA. An expired certificate still starts, with a
warning, so browsers can name the cause. A new public hostname therefore
needs a new certificate first: `tls-request`, sign, `tls-install`, then
install with the new hostname.

### Renewal

```text
active:  server.key + server.crt     nginx keeps serving these
pending: request.key (+ the CSR)     until the signed certificate passed every check
```

1. `tls-request` makes a new `request.key` and a CSR; the active pair
   is untouched. Nothing makes it by itself: the single host's nightly
   backup run (`todo-backup.timer`) fails below 30 days, so it shows in
   `systemctl --user --failed`, and on a DR pair `app_dr.py check` does.
2. The CA signs the CSR (`app_ca.py sign`, or `sudo todo-ca-sign` in v1).
3. `tls-install` checks the certificate against the pending key. A
   certificate that fits neither key, misses a hostname or chains to another
   CA changes nothing, and the pending key keeps waiting.
4. Only then `request.key` becomes `server.key` and the new certificate
   `server.crt`, nginx's Kube secret follows, nginx restarts (with the TLS
   volume: reloads), and `tls-install` waits until it serves the new
   fingerprint for every hostname.

nginx reads the pair only when its pod is created (with the TLS volume: when
it starts or reloads); the Kube secret changes only after the raw secrets
passed every check, and a start that finds a pair that does not fit fails
closed. `tls-status` shows the mode and the days left at any time.

The CA itself is not renewed automatically: issue a new CA well before its 20
years end, and give clients the new `ca.crt` before switching hosts to it.
Going back to local mode is deliberate, not a fallback: `uninstall
--remove-data` removes the TLS secrets (and the TLS volume), and the next
install starts a new demo CA.

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

### Podman secrets or the TLS volume

Both keep the same files with the same checks; they differ in how nginx
gets them and what a change costs:

| | Podman secrets (default) | TLS volume |
|---|---|---|
| Where the files are | one Podman secret per file | `todo-nginx-data` |
| What nginx mounts | the Kube secret `todo-kube-proxy-tls-secret`, read-only | the volume, read-only |
| Who makes the demo files | the installer, before nginx starts | the `nginx-tls` init container, at every pod start |
| The demo CA's key | its own secret, never mounted into nginx | in the volume beside nginx's files |
| A new certificate takes effect | after a restart (a new pod): a few seconds without HTTPS | after a reload, no interruption |
| Renewing the demo leaf | `install` or `tls-renew` | a pod restart |
| On a DR standby before a failover | the proxy image | the proxy image and the volume |

Podman secrets are the default because they show the one mechanism this
project uses for every credential (see [SECRETS](SECRETS.md)), and the demo
CA's key no longer shares a directory with nginx's files. The volume stays
for a host that cannot accept the restart.

| What | Where |
|---|---|
| database and application credentials | Podman secrets |
| a signing certificate delivered as a file (for example `signing-cert.pfx`) | Podman secret / file secret |
| nginx's `server.key`, `server.crt`, `ca.crt` | Podman secrets (or the TLS volume) |
| the CA's `ca.key` | CA storage, outside Podman |

### Switching back to the TLS volume

The volume's code is all kept: `app_installer/tls.py`, the entrypoint's
provision role, the DR steps for the volume, and their tests. To use it on a
host again:

1. In `deploy/manifests/shared-proxy.yaml.j2`, remove `#~ ` from every line
   that starts with it (the claim, the `nginx-tls` init container and its
   annotation, the `tls-data` claim), and delete the lines from
   `# BEGIN secret` to `# END secret`.
2. In `deploy/installer/app_installer/settings.py`, set
   `NGINX_TLS_STORAGE = "volume"`.
3. Build the bundle and the operations package, and install them.

The init container then makes a new demo CA in the volume, or uses what an
earlier volume still holds; for a provided certificate run `tls-request`,
sign and `tls-install` again. `tests/test_proxy_configuration.py` checks that
step 1 gives exactly the volume manifest, and the volume tests (test_tls.py,
the DR test_nginx_tls.py) run with step 2.

### nginx reads its files read-only

nginx runs with `TODO_TLS_ROLE=serve` and its files mounted read-only: it
only checks that the files it is about to serve fit, and never writes them.
With Podman secrets nothing in the pod writes them; the installer's
throwaway containers make them, and the pod has no init container. So a
compromised nginx can read its own key but cannot replace it or the CA
certificate beside it, and never sees the demo CA's key.

```text
install, tls-renew, tls-request, tls-install   make the secrets (throwaway containers, no network)
nginx                                          RO   (serve)
```

With the TLS volume, the `nginx-tls` init container (`TODO_TLS_ROLE=provision`)
mounts the volume read-write and issues, renews or checks at every pod start.

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

Podman labels the secrets it mounts, and the TLS volume is a Podman-managed
volume with container labeling and rootless UID mapping; neither needs a host
path. A host bind mount would additionally require a correct SELinux label
such as `:Z`; permissive Unix modes do not bypass SELinux. The installer's
openssl containers work in a tmpfs, so they need no host path either.

OpenSSL and the certificate scripts execute inside the OCI container, so host
`fapolicyd` does not evaluate them. Host-side PKI automation is
different: project-owned scripts must receive exact trust or, for a larger
Oracle Linux deployment, be packaged as signed RPM content and installed
through DNF. Using an RPM-provided OpenSSL binary does not automatically trust
an untrusted host-side script that invokes it.

Never disable SELinux or fapolicyd to make certificate deployment work.
