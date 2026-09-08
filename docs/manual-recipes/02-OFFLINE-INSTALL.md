# Offline install on one VM

This demonstrates four pods delivered without target internet access, trusted
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

The build needs Helm to render the Kubernetes manifests before packaging;
Helm is not needed on the target VM. Install it (matching the version pinned
in CI, `.github/workflows/clean-install.yml`), then build:

```bash
curl --fail --location --output /tmp/helm.tar.gz \
  https://get.helm.sh/helm-v4.2.4-linux-amd64.tar.gz
echo "c306b46f719b0a4da32d0f78ee21bf90ce8d602f15b22ab753f0674d1670a7f3  /tmp/helm.tar.gz" | sha256sum -c
tar -xzf /tmp/helm.tar.gz -C /tmp
export PATH="/tmp/linux-amd64:$PATH"

offline/build-bundle.sh
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
plus the installer and manifest files (see `offline/README.md`).

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

The first run asks for a PostgreSQL bootstrap password and a temporary
Keycloak admin password. Other database passwords are generated automatically
and stored as Podman secrets.

The installer verifies the bundle, loads container images and runs the
Ansible deployment. It does not contact a container registry or the Python
package index.

## 5. Check that the application is running

```bash
systemctl --user is-active \
  todo-postgres.service \
  todo-keycloak.service \
  todo-app.service \
  shared-proxy.service

podman ps
podman secret ls

curl --fail http://127.0.0.1:8080/ready
```

All four services should be active, and the readiness check should succeed.

## 6. Open Todo from the laptop

This lab setup uses the hostname `todo.test`. Add the VM's IP to the laptop's
`/etc/hosts`:

```text
192.168.1.50 todo.test
```

Then open <https://todo.test:8443>.

You need to trust the public demo CA from the installation to avoid
certificate warnings. Follow the repository's [TLS guide](../TLS.md) to
export and install the CA certificate — or use
`scripts/trust-serving-ca.sh todo@192.168.1.50` from the laptop, which does
the fetch, fingerprint verification and trust-store update in one step.

**Note:** the installation creates the Keycloak realm and client, but no
regular Todo user. Create a user in Keycloak afterward to be able to log in
and change Todos.

The detailed source for this installation is the
[offline bundle guide](../../offline/README.md). On a repeat installation you
must use the same `--publish-address`, otherwise publication falls back to
localhost.

## 7. Try public reads and login

From the laptop, verify `curl --fail https://todo.test:8443/ready` succeeds
without `-k`. Open Todo: reads should work before login. In Keycloak's admin UI,
create a lab user with email, first/last name, a non-temporary password and no
required actions. Log in through Todo, create a uniquely named test Todo, edit
it, reload the page and verify it remains. Delete only your own test item.
Log out and verify writes require login again.

If login loops or fails, inspect discovery at
`https://todo.test:8443/auth/realms/todo/.well-known/openid-configuration`:
issuer must be `https://todo.test:8443/auth/realms/todo`. Check the client mapping,
CA trust and user profile; do not disable certificate verification.

On the VM, `podman exec nginx nginx -t -c /etc/todo-nginx/nginx.conf` should
report success. `nginx` belongs to `shared-proxy.service`; its CA persists in
`todo-nginx-data`. `todo-frontend` only serves HTTP static files.
