# Offline install on one VM

This demonstrates seven pods (Todo, Notes, Keycloak, their three databases and
the shared proxy) delivered without target internet access, trusted
HTTPS, public reads and authenticated writes. Use a clean lab VM, enough disk,
rootless Podman on the build laptop, verified SSH and the prepared service user.
Building runs on the laptop; installation and service checks run inside the VM.
Do not run this initial installer on an existing replicated pair. If a command
fails, stop and inspect its output and the affected user-service journal before
continuing; use [troubleshooting](../ACCEPTANCE-TROUBLESHOOTING.md).

**Precondition:** the repository, on branch `feature/podman-kube`, is on your
Linux laptop. The laptop has internet access and is compatible with the
target VM's CPU architecture. The target VM is prepared per
[Prepare an Oracle Linux 9 VM](01-PREPARE-VM.md).

## 1. Build the offline bundle on the laptop

From the repository root:

```bash
git switch feature/podman-kube
```

The build renders the Kubernetes manifests with Jinja2 on the laptop before
packaging; this needs Python and the `jinja2`/`PyYAML` packages there. The
target VM's own installer also renders `.kube` units at install time and
needs the same two packages — [Prepare an Oracle Linux 9 VM](01-PREPARE-VM.md)
already installs them:

```bash
deploy/offline/build-bundle.sh
```

Building the bundle does not need `ansible-core` or a Python virtualenv on the
laptop; that is only needed if you also want to run `ansible-playbook`
directly from the laptop (see the [DR walkthrough](03-DR-TWO-VM.md)). The
target VM uses its own RPM-installed `ansible-core`, not anything from the
laptop.

The build produces:

```text
dist/todo-offline-m12.tar.gz
dist/todo-offline-m12.tar.gz.sha256
```

The bundle contains the backend, frontend, shared proxy, Keycloak and PostgreSQL images,
plus the installer and manifest files (see `deploy/offline/README.md`).

## 2. Copy the bundle to the VM

From the laptop, substitute your username and IP:

```bash
scp dist/todo-offline-m12.tar.gz \
    dist/todo-offline-m12.tar.gz.sha256 \
    todo@192.168.1.50:/home/todo/
```

Transfer both the archive and the checksum file over a trusted channel.

## 3. Verify and extract on the VM

Log in as `todo`:

```bash
ssh todo@192.168.1.50
```

Run:

```bash
cd /home/todo

sha256sum -c todo-offline-m12.tar.gz.sha256
tar -xzf todo-offline-m12.tar.gz

cd todo-offline-m12
sh ./preflight.sh
```

Preflight checks prerequisites without changing host configuration. When
`fapolicyd` is active, run the scripts through the system shell with `sh`, as
the documentation shows.

## 4. Install the application

To make HTTPS reachable from the laptop, use the VM's own IP as the publish
address:

```bash
sh ./install.sh --publish-address 192.168.1.50
```

Every password — PostgreSQL bootstrap, database-role and the Keycloak
administrator credential — is generated automatically on first run; none are
ever prompted for or printed. All values are stored as host-local Podman
secrets.

The installer verifies the bundle, loads container images and installs the
workload itself; it does not invoke Ansible (that is DR/multi-host only, see
the [DR walkthrough](03-DR-TWO-VM.md)) or contact a container registry or the
Python package index.

## 5. Check that the application is running

```bash
systemctl --user is-active \
  shared-proxy.service \
  todo-app.service \
  notes-app.service \
  keycloak.service \
  todo-postgres.service \
  notes-postgres.service \
  keycloak-postgres.service

podman ps
podman secret ls

curl --fail http://127.0.0.1:8080/ready
```

All seven services should be active, and the readiness check should succeed.

## 6. Open Todo from the laptop

This lab setup uses the hostnames `todo.test` and `notes.test`. Add the VM's IP
to the laptop's `/etc/hosts`:

```text
192.168.1.50 todo.test notes.test
```

Then open <https://todo.test:8443>.

You need to trust the public demo CA from the installation to avoid
certificate warnings. Follow the repository's [TLS guide](../TLS.md) to
export and install the CA certificate — or use
`deploy/scripts/trust-serving-ca.sh todo@192.168.1.50` from the laptop, which does
the fetch, fingerprint verification and trust-store update in one step.

**Note:** the installation creates the Keycloak realm and client, but no
regular Todo user. Create a user in Keycloak afterward to be able to log in
and change Todos.

The Keycloak admin console is at `https://todo.test:8443/auth/admin/`. Its
generated admin password is a Podman secret on the VM; read it only when
needed, and never into a file or shell history you keep:

```bash
podman secret inspect --showsecret --format '{{.SecretData}}' keycloak-admin-password
```

The detailed source for this installation is the
[offline bundle guide](../../deploy/offline/README.md). On a repeat installation you
must use the same `--publish-address`, otherwise publication falls back to
localhost.

## 7. Try public reads and login

From the laptop, verify `curl --fail https://todo.test:8443/ready` and
`curl --fail https://notes.test:8443/ready` both succeed without `-k`. Open
Todo: reads should work before login. In Keycloak's admin UI, create a lab
user with email, first/last name, a non-temporary password and no required
actions. Log in through Todo, create a uniquely named test Todo, edit it,
reload the page and verify it remains. Delete only your own test item. Log
out and verify writes require login again.

Then open <https://notes.test:8443> in the same browser session: it should
already show you logged in, without a second password prompt (Todo and Notes
share one Keycloak login). Create, edit and delete a test note the same way.
A Todo access token must not authorize a Notes write, or vice versa; the two
frontends use separate token audiences even though the login is shared.

If login loops or fails, inspect discovery at
`https://todo.test:8443/auth/realms/todo/.well-known/openid-configuration`:
issuer must be `https://todo.test:8443/auth/realms/todo`. Check the client mapping,
CA trust and user profile; do not disable certificate verification.

On the VM, `podman exec nginx nginx -t -c /etc/todo-nginx/nginx.conf` should
report success. `nginx` belongs to `shared-proxy.service`; its CA persists in
`todo-nginx-data`. `todo-frontend` only serves HTTP static files.
