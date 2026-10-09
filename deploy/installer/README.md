# Portable single-host installer

Python 3.9+ is the only runtime dependency of an offline install; build and
dev mode also need Jinja2 and PyYAML, and the DR tools need PyYAML. Podman runs rootless;
server mode also needs a working user systemd manager. Rendering only runs in
build mode through `deploy/scripts/render-kube-runtime.sh`, and for a bundle
through `app_installer.bundle`. Both delegate workload selection to the App
registry. An offline install never renders: it fills the target values into
files the build rendered (`target_render.py`, standard library only), and so
do the DR tools on a primary and a standby.

`apps.APPS` registers Todo and Notes. Each App owns its derived image, secret,
manifest, service and volume names. Single-host installs run seven pods; Keycloak,
its own `keycloak-postgres` database and the proxy run once. Both apps share the
`todo` realm but have independent clients and PostgreSQL instances.
`apps.REPLICATED_DATABASES` (todo, notes, keycloak) is the DR group.

Build mode renders on the host, so it needs OS-managed Jinja2 and PyYAML.
From a checkout or extracted package:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
python3 -m app_installer install --mode server --deployment-mode build
python3 -m app_installer install --mode dev --deployment-mode build
python3 -m app_installer down
python3 -m app_installer uninstall
```

Map `todo.test` and `notes.test` to the serving host (both to `127.0.0.1` for
direct development) and trust the proxy CA as described in [TLS](../../docs/TLS.md).
Both hosts use one SAN certificate and HTTPS port 8443. The installer keeps
nginx's CA and certificate as Podman secrets (`tls_secrets.py`) and makes them
with the OpenSSL in the proxy image, so the host needs none; `tls_store.py`
chooses between those secrets and the earlier TLS volume (`tls.py`).
`python3 -m app_installer tls-renew` renews nginx's demo certificate and
restarts nginx, also on a DR host.

Server and dev are alternative lifecycle owners. Use separate Podman user stores;
do not run dev cleanup against a server deployment. `dev-up.sh` and `dev-down.sh`
are compatibility wrappers around these commands.

Offline installation consumes the bundle's pre-rendered files and OCI archives
without network access, in server mode only:

```bash
python3 -m app_installer install --mode server --deployment-mode offline \
  --bundle-dir /path/to/todo-offline-m12 --publish-address 192.168.0.102 \
  --target-external-hostname todo.example.org --target-notes-hostname notes.example.org
```

The hostname options are optional, one per app; the host records the names it
installed with (`~/.config/todo/target-values.json`), so a later install keeps
them. The supported target values, their sources and checks are in
[the offline README](../offline/README.md#target-values).

Use `--project-root` when templates live somewhere other than the source package
root, including when using an editable/pip installation from another working
directory. A connected development environment can use `pip install -e deploy/installer`.
On hardened/offline targets use OS-managed Python and verified exact-file
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

## Nightly backups

A server install turns on `todo-backup.timer`, which runs
`python3 -m app_installer backup nightly --keep-days 7` from this installer's
directory every night: a verified base backup of every installed database,
taken inside its container into its backup volume, then deletion of those
older than 7 days (`backup.py`). `backup create` takes one now, and
`backup restore --confirm-restore <hostname>` puts every database back to its
latest backup (single host only). Uninstall turns the timer off and keeps the
backups. The [offline README](../offline/README.md#nightly-backups) has the
operator's view.

## Shared workload API

`workloads.install_postgres`, `install_application`, `install_keycloak` and
`install_shared_proxy`
accept project, Quadlet, runtime and rendered-manifest directories, or an
offline bundle's filled-in files (`target=`). They return whether manifests,
network or unit definitions changed. They always reload user systemd; they
never restart services themselves.
Callers control safe stop/start ordering, taken from `apps.workloads()`: the
seven pods in start order, each with its Kube YAML and ConfigMap files and
whether a start waits for it to be healthy; stop is the reverse
(`apps.services()`). Secret creation and obsolete `.volume`
file cleanup do not affect the definition-change flag used by DR.

`install.install` and the DR building blocks in `app_dr_host` (replication,
reseed, promoted deploy, under [deploy/dr](../dr/README.md)) call these
functions directly; there is no command line for a single workload, and this
package imports nothing from `app_dr_host`. LAN replication publication is
accepted only for the DR group's databases.
Runtime directories must be exactly `quadlet-dir/todo-kube-runtime`; no new
`.volume` units are installed. Canonical Jinja templates stay outside the Python
package, under the supplied project's `deploy/quadlet` directory.

## Tests

```bash
python3 -m unittest discover --start-directory deploy/installer/tests
python3 -m unittest discover --start-directory deploy/dr/tests
python3 -m unittest discover --start-directory tests
```

CI also lints with ruff (`ruff.toml`) and type-checks with pyright in basic
mode (`pyrightconfig.json`, Jinja2 and PyYAML installed); both run the same
way locally, from the repository root:

```bash
ruff check todo-backend notes-backend e2e deploy/scripts deploy/installer deploy/dr tests
pyright
```

Most tests mock only runtime commands and use scratch directories. Project
tests run app-ops against fake hosts, verify repeat change facts, and
build/examine both delivery archives. Real multi-app dev/server, offline loading, persistence, trusted browser SSO and
idempotency were additionally tested in a separate Fedora 44 VM with rootless
Podman 5.8.1 and SELinux enforcing; see [results](../../docs/history/RESULTS.md).
This does not replace Oracle Linux/fapolicyd or two-host DR acceptance.

`render.py` parses values and validates every rendered manifest with PyYAML, and
`replication.py` consumes canonical PVC YAML the same way, so PyYAML
(`python3-pyyaml` or the platform's equivalent package) is now a base
dependency everywhere build-mode rendering or DR runs; an offline target
install, which never renders, does without it and without Jinja2, and DR
needs no Jinja2. Jinja2 and
PyYAML are imported only where rendering happens (`manifests.py`, `quadlet.render`,
`render.py`, `bundle.py`); `test_offline_install.py` runs the whole offline install in
a Python process where neither can be imported. The replication
registry contains all three databases; see the
[phased DR checkpoints](../../docs/MULTI-APP-DR-VERIFICATION.md).
