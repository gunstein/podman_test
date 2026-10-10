# Portable single-host installer

Python 3.9+ is the only runtime dependency of an offline install; build and
dev mode also need Jinja2 and PyYAML, and the DR tools need PyYAML. Podman runs rootless;
server mode also needs a working user systemd manager. Rendering only runs in
build mode, where the install renders the platform it installs
(`render.render`), and for a bundle through `app_installer.bundle`;
`deploy/scripts/render-kube-runtime.sh` renders `platform.yaml`'s apps by hand.
An offline install never renders: it fills the target values into
files the build rendered (`target_render.py`, standard library only), and so
do the DR tools on a primary and a standby.

One installation is an `apps.Platform`: its apps, in start order, and
Keycloak's default hostname. A build reads it from `platform.yaml` at the
project root and each app's `app.yaml` (`platform_file.py`; today Todo and
Notes, in `examples/`); an offline bundle carries the one it was built for in `bundle.json`,
and an install records it on the host (`~/.config/platform/platform.json`),
which every later command on the host (backup, uninstall, the DR tools) reads
instead of a list in the code. That record is written before an install
changes anything, so it says what the host may hold, not that the install
succeeded; the hostnames it was installed with are recorded only after
success, in `target-values.json` (below). Each App owns its derived image, secret,
manifest, service and volume names. Single-host installs run seven pods; Keycloak,
its own `keycloak-postgres` database and the proxy run once. Both apps share the
`todo` realm but have independent clients and PostgreSQL instances.
`Platform.replicated_databases` (todo, notes, keycloak) is the DR group.

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
  --bundle-dir /path/to/platform-offline-m12 --publish-address 192.168.0.102 \
  --target-hostname identity=auth.example.org --target-hostname todo=todo.example.org \
  --target-hostname notes=notes.example.org
```

The hostname options are optional: one for Keycloak, one per app. The host records the names it
installed with (`~/.config/platform/target-values.json`), so a later install keeps
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
mode; offline mode rejects it. The external proxy port is `publicPort` in
`platform.yaml` (build mode) or the bundle's (offline), the same number the
rendered URLs use; `--service-port` may only repeat it, and a different one is
refused. Loopback bindings remain 8080 and 8443, matching the canonical template. The port has
no effect without a non-default `--publish-address`: with the default loopback
address there is nothing external to bind it to. `--publish-address` must be
the host's own address, never a wildcard (`0.0.0.0` or `::`): the template
always keeps the fixed loopback binding alongside it, and a wildcard would try
to bind the same port twice.

Uninstall preserves database/backup volumes, nginx's TLS state and credentials
by default, and refuses hosts with replication secrets or DR/backup markers.
It always removes the Kube secrets' volumes, the plain-text copies `podman kube
play` makes of mounted secrets ([Secrets](../../docs/SECRETS.md#a-kube-secret-also-lives-in-a-volume)),
and an old per-container install from the tag `quadlet-reference-v1` (its
`.container` files, `todo.network`, the network `todo-network`, its containers
and `localhost/todo-keycloak:m12`), which it names. `install` keeps refusing a
host with that old install: removing it is a deliberate `uninstall`.
`--remove-data` explicitly removes database data, TLS state and the credentials,
not the backup volumes. Only `--remove-data --remove-backups` removes those
too, so that one command empties a host:

```bash
python3 -m app_installer uninstall --remove-data --remove-backups
podman ps -a; podman volume ls; podman secret ls   # nothing of the project
```

Nothing can be restored afterwards. The PostgreSQL image stays (it is not the
project's own), and on a DR host the command refuses like every uninstall.

## What scripts read from the platform

Shell scripts ask the installer instead of keeping their own list of apps:

```bash
python3 -m app_installer platform hostnames                  # auth.test todo.test notes.test
python3 -m app_installer platform hostname notes             # one: identity or an app
python3 -m app_installer platform public-port --environment local
python3 -m app_installer platform host-ports --bundle-dir .  # "CONTAINER PORT..." per line
```

Without `--bundle-dir` they read `platform.yaml` (PyYAML); with it, the
bundle's `bundle.json` (standard library only, as `preflight.sh` does on an
offline host). `wait-ready.sh` gets its pods, containers and hostnames as
arguments from its Python callers (`apps.Platform.ready`).

## Nightly backups

A server install turns on `platform-backup.timer`, which runs
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
Callers control safe stop/start ordering, taken from `Platform.workloads()`: the
seven pods in start order, each with its Kube YAML and ConfigMap files and
whether a start waits for it to be healthy; stop is the reverse
(`Platform.services()`). Every Kube YAML file is named after its pod's name,
then its component (`todo-postgres.yaml`, `notes-config.yaml`); an install
removes todo's files under their earlier names (`postgres.yaml`,
`config.yaml`, `app.yaml`), and bundles of the earlier format version are
refused. Secret creation and obsolete `.volume`
file cleanup do not affect the definition-change flag used by DR.

`install.install` and the DR building blocks in `app_dr_host` (replication,
reseed, promoted deploy, under [deploy/dr](../dr/README.md)) call these
functions directly; there is no command line for a single workload, and this
package imports nothing from `app_dr_host`. LAN replication publication is
accepted only for the DR group's databases.
Runtime directories must be exactly `quadlet-dir/platform-kube-runtime`; no new
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
a Python process where neither can be imported. The replicated
group (`Platform.replicated_databases`) holds all three databases; see the
[phased DR checkpoints](../../docs/MULTI-APP-DR-VERIFICATION.md).
