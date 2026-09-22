# Portable single-host installer

Python 3.9+ and Jinja2 are the only runtime dependencies. Podman runs rootless;
server mode also needs a working user systemd manager. Helm runs only in build
mode through the unchanged `deploy/scripts/render-kube-runtime.sh` script.

From a checkout or extracted package with OS-managed Jinja2:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
python3 -m todo_installer install --mode server --deployment-mode build
python3 -m todo_installer install --mode dev --deployment-mode build
python3 -m todo_installer down
python3 -m todo_installer uninstall
```

Server and dev are alternative lifecycle owners. Use separate Podman user stores;
do not run dev cleanup against a server deployment. `dev-up.sh` and `dev-down.sh`
are compatibility wrappers around these commands.

Offline installation consumes existing YAML and OCI archives without Helm or
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

The installer prompts only for missing database/bootstrap and Keycloak admin
secrets, with terminal echo disabled. Non-interactive calls fail if these are
missing; provision raw Podman secrets first. It generates 32-character
alphanumeric runtime-role passwords only when missing. Existing raw and
Kube-compatible secrets are never rotated. No secret payload is written to disk.

`--refresh-images` rebuilds application images and pulls PostgreSQL in build
mode; offline mode rejects it. `--service-port` selects the external proxy port;
loopback bindings remain 8080 and 8443, matching the canonical template.

Uninstall preserves database/backup volumes and credentials by default, removes
TLS state, and refuses hosts with replication secrets or DR/backup markers.
`--remove-data` explicitly removes database data and its credentials, never the
backup volume.

## Shared workload API

`workloads.install_postgres`, `install_application` and `install_shared_proxy`
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
parity tests require real Helm and Ansible and compare every Quadlet byte for
external HTTPS, loopback with replication, and an unset PostgreSQL address.
Project tests execute the actual Ansible staging bridge, verify repeat change
facts, and build/examine both delivery archives. Real rootless Podman, systemd,
SELinux, fapolicyd and two-host DR still require the VM acceptance run.
