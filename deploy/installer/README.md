# Portable single-host installer

Python 3.9+ and Jinja2 are the only runtime dependencies. Podman runs rootless;
server mode also needs a working user systemd manager. Rendering only runs in
build mode through `deploy/scripts/render-kube-runtime.sh`. The script delegates
workload selection to the App registry; targets in offline mode never render.

`apps.APPS` registers Todo and Notes. Each App owns its derived image, secret,
manifest, service and volume names. Single-host installs run seven pods; Keycloak,
its own `keycloak-postgres` database and the proxy run once. Both apps share the
`todo` realm but have independent clients and PostgreSQL instances.
`apps.REPLICATED_DATABASES` (todo, notes, keycloak) is the DR group.

From a checkout or extracted package with OS-managed Jinja2:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
python3 -m todo_installer install --mode server --deployment-mode build
python3 -m todo_installer install --mode dev --deployment-mode build
python3 -m todo_installer down
python3 -m todo_installer uninstall
```

Map `todo.test` and `notes.test` to the serving host (both to `127.0.0.1` for
direct development) and trust the proxy CA as described in [TLS](../../docs/TLS.md).
Both hosts use one SAN certificate and HTTPS port 8443.

Server and dev are alternative lifecycle owners. Use separate Podman user stores;
do not run dev cleanup against a server deployment. `dev-up.sh` and `dev-down.sh`
are compatibility wrappers around these commands.

Offline installation consumes existing YAML and OCI archives without
network access:

```bash
python3 -m todo_installer install --mode server --deployment-mode offline \
  --bundle-dir /path/to/todo-offline-m12 --publish-address 192.168.0.102
```

Use `--project-root` when templates live somewhere other than the source package
root, including when using an editable/pip installation from another working
directory. A connected development environment can use `pip install -e deploy/installer`.
On hardened/offline targets use OS-managed Python/Jinja2 and verified exact-file
trust as described in [FAPOLICYD.md](../offline/FAPOLICYD.md).

The installer generates every missing raw secret - database/bootstrap and
Keycloak admin included - as a 32-character alphanumeric password; none are
ever prompted, printed or require an interactive terminal. Existing raw and
Kube-compatible secrets are never rotated. No secret payload is written to disk.

Dev keeps a manifest fingerprint beside the Quadlet directory. An unchanged
install with running pods returns unchanged and preserves container IDs;
`--refresh-images` also recreates dev pods. Existing unmanaged pods require
explicit cleanup before the installer takes ownership.

`--refresh-images` rebuilds application images and pulls PostgreSQL in build
mode; offline mode rejects it. `--service-port` selects the external proxy port;
loopback bindings remain 8080 and 8443, matching the canonical template. It has
no effect without a non-default `--publish-address`: with the default loopback
address there is nothing external to bind it to. `--publish-address` must be
the host's own address, never a wildcard (`0.0.0.0` or `::`): the template
always keeps the fixed loopback binding alongside it, and a wildcard would try
to bind the same port twice.

Uninstall preserves database/backup volumes and credentials by default, removes
TLS state, and refuses hosts with replication secrets or DR/backup markers.
`--remove-data` explicitly removes database data and its credentials, never the
backup volume.

## Shared workload API

`workloads.install_postgres`, `install_application`, `install_keycloak` and
`install_shared_proxy`
accept project, Quadlet, runtime and rendered-manifest directories. They return
whether manifests, network or unit definitions changed. They always reload user
systemd, matching the former roles; they never restart services themselves.
Callers control safe stop/start ordering. Secret creation and obsolete `.volume`
file cleanup do not affect the definition-change flag used by DR.

```bash
python3 -m todo_installer install-workload postgres \
  --project-root /path/to/package \
  --quadlet-dir "$HOME/.config/containers/systemd" \
  --kube-runtime-dir "$HOME/.config/containers/systemd/todo-kube-runtime" \
  --rendered-manifest-dir /path/to/rendered \
  --postgres-publish-address 192.168.0.102
```

The workload CLI emits one JSON result on stdout and diagnostics on stderr.
Use `--app notes` for an independent Notes postgres/application definition;
`--app todo` is the default used by DR. The Todo application CLI also installs
shared Keycloak for existing DR callers. Notes LAN replication publication is
refused until the separate DR phase is implemented.
Application/proxy calls also accept `--publish-address` and `--service-port`.
Runtime directories must be exactly `quadlet-dir/todo-kube-runtime`; no new
`.volume` units are installed. Canonical Jinja templates stay outside the Python
package, under the supplied project's `deploy/quadlet` directory.

## Tests

```bash
python3 -m unittest discover --start-directory deploy/installer/tests
python3 -m unittest discover --start-directory tests
```

Most tests mock only runtime commands and use scratch directories. Rendering
parity tests require real Ansible and compare every Quadlet byte for
external HTTPS, loopback with replication, and an unset PostgreSQL address.
Project tests execute the actual Ansible staging bridge, verify repeat change
facts, and build/examine both delivery archives. Real multi-app dev/server, offline loading, persistence, trusted browser SSO and
idempotency were additionally tested in a separate Fedora 44 VM with rootless
Podman 5.8.1 and SELinux enforcing; see [results](../../docs/history/RESULTS.md).
This does not replace Oracle Linux/fapolicyd or two-host DR acceptance.

`render.py` parses values and validates every rendered manifest with PyYAML, and
`replication.py` consumes canonical PVC YAML the same way, so PyYAML
(`python3-pyyaml` or the platform's equivalent package) is now a base
dependency everywhere build-mode rendering or DR runs; only a purely offline
target install, which never renders, can do without it. The replication
registry contains all three databases; see the
[phased DR checkpoints](../../docs/MULTI-APP-DR-VERIFICATION.md).
