# Offline bundle

The bundle installs Todo and Notes (both applications, shared identity and
shared proxy) without contacting a container registry or Python package
index. It does not install operating-system prerequisites.

## Target prerequisites

The target machine must already provide:

- Podman configured for the current non-root user
- Rootless user namespaces, normally backed by entries in `/etc/subuid` and
  `/etc/subgid`
- Podman's Quadlet systemd generator
- A working `systemctl --user` session
- OS-managed Python 3.9+ (the standard library only): the bundle carries
  every Kube YAML file and `.kube` unit already rendered, and the install only
  fills in the target values (see [Target values](#target-values)); neither
  Jinja2 nor PyYAML is needed
- `/bin/sh`, `tar` and `sha256sum`
- Free host ports 5432, 5433, 5434, 8080 and 8443 on a clean target (8000 is internal to the app pod)

A target that will also run DR through the operations package (either host in
the [two-VM walkthrough](../../docs/manual-recipes/03-DR-TWO-VM.md)) needs
`python3-pyyaml`: the replication commands parse the canonical PVC YAML for
standby bootstrap and rebuild. The DR tools render nothing on the host: they
install the operations package's pre-rendered files, filled in the same way
(see [Primary and standby](#primary-and-standby)), so they need no Jinja2. A DR
host also needs `openssl`, which issues the certificates that encrypt
replication. [Prepare an Oracle Linux 9 VM](../../docs/manual-recipes/01-PREPARE-VM.md)
installs these packages on every target so this does not need revisiting later.

The Kube runtime requires the tested Podman 5.8.2 platform, systemd 255 and
Python. DR operations use app-ops from the separate operations package. The
bundle must be built on a machine compatible with the target's CPU architecture.

For a comfortable demo VM, provide at least 4 GiB memory and 10 GiB free disk.
The preflight script reports available resources but treats these figures as
recommendations rather than hard requirements.

## Build on the connected machine

From the project root:

```bash
deploy/offline/build-bundle.sh
```

The connected build machine renders everything with Jinja2 before packaging
(`app_installer.bundle`); the isolated target receives plain files:

| In the bundle | What it is |
|---|---|
| `generated/target/manifests/` | Every Kube YAML file, with `${TARGET_EXTERNAL_HOSTNAME}` and `${TARGET_NOTES_HOSTNAME}` where each app's public hostname goes |
| `generated/target/quadlet/` | Every `.kube` unit and `app-network.network`; the proxy unit also publishes HTTPS on `${TARGET_PUBLISH_ADDRESS}` |
| `generated/target/quadlet/local-only/` | The proxy unit for a host that publishes only on 127.0.0.1 |
| `generated/target/quadlet/replicated/` | The database units of a DR primary, which also publish replication on `${TARGET_PUBLISH_ADDRESS}` |
| `bundle.json` | Format and version (`todo-offline-bundle`, 3), where each of the above is, the apps, the HTTPS port and the default target values |
| `generated/kube-runtime/` | The Kube YAML with the build's hostnames, for reading and comparison; nothing installs it |

The build checks that putting the default hostnames into the target manifests
gives exactly the normal render, so a placeholder only stands where a
hostname stood. `VERSION` and `SHA256SUMS` cover every file, `bundle.json` and
the target files included.

This builds the backend, frontend, shared proxy and Keycloak images, pulls PostgreSQL, and
creates both the archive and its external checksum:

```text
dist/todo-offline-m12.tar.gz
dist/todo-offline-m12.tar.gz.sha256
```

Build the bundle on a machine compatible with the offline target. Its `VERSION`
file records the source Git revision and clean/dirty build state. Deploy a
reviewed `clean` artifact; `dirty` is diagnostic provenance, not a release
identifier.

## Install on the offline machine

Copy the archive and checksum to the target through the trusted transfer path.
Verify the archive before extracting or running any bundled code:

```bash
sha256sum -c todo-offline-m12.tar.gz.sha256
tar -xzf todo-offline-m12.tar.gz
cd todo-offline-m12
# With active fapolicyd, first apply the exact-file trust steps below.
sh ./preflight.sh
sh ./install.sh
```

For a separate lab client, use `sh ./install.sh --publish-address 192.168.0.102`;
add `--target-external-hostname NAME` and `--target-notes-hostname NAME` for
public hostnames other than the bundle's defaults (see below).
The address must belong to the target VM. The default publishes HTTPS on
localhost only. Use the same argument on every repeat installation; omitting
it restores localhost-only publication. Only HTTPS is exposed externally;
health HTTP and the database remain on localhost. The installer does not change
firewalld. Allow TCP 8443 only from the intended client, following
`docs/ACCEPTANCE.md`.

Running the scripts through the trusted system shell is intentional. On a
machine with active `fapolicyd`, newly extracted scripts cannot yet be executed
directly with `./script.sh`. The RPM-managed shell reads them as data. The
installer does not add the extracted bundle to the trust database. Its Python
sources need the exact-file trust described below before installation.

The preflight script does not change host configuration. It checks Podman,
rootless user namespaces, Quadlet, the user systemd manager and the ports.

### Target values

The bundle's files are complete except for the values only the target knows.
`install.sh` fills in exactly these placeholders, with the Python standard
library (`app_installer/target_render.py`), and nothing else: `$HOME`,
`${DATABASE_PASSWORD}` and every other dollar expression stay as they are, and
nothing is passed through a shell or expanded from the environment.

| Placeholder | Value | Where it comes from, first match wins | Checked as |
|---|---|---|---|
| `${TARGET_EXTERNAL_HOSTNAME}` | The public hostname of the Todo app and of Keycloak: nginx `server_name`, the TLS certificate, the OIDC issuer, `KC_HOSTNAME` and the Keycloak client's redirect URL | `--target-external-hostname`, then the environment variable `TARGET_EXTERNAL_HOSTNAME`, then the host's record, then the bundle's default (`runtime.publicHostname` in the build's `values.yaml`, `todo.test`) | A DNS name: lowercase labels of letters, digits and inner hyphens |
| `${TARGET_NOTES_HOSTNAME}` | The public hostname of the Notes app: its nginx `server_name`, the TLS certificate and its Keycloak client's redirect URL | `--target-notes-hostname`, then `TARGET_NOTES_HOSTNAME`, then the host's record, then the bundle's default (the app registry's `notes.test`) | As above |
| `${TARGET_PUBLISH_ADDRESS}` | The host IPv4 address nginx publishes HTTPS on (and, on a DR primary, PostgreSQL replication) | `--publish-address` (default `127.0.0.1`, which selects the local-only proxy unit); never the environment, a record or a default | A host IPv4 address, not a wildcard, multicast or reserved one |

Every app other than Todo gets its own `${TARGET_<APP>_HOSTNAME}` and
`--target-<app>-hostname`, from the app registry (`apps.py`). The machine's own
hostname or FQDN is never used as a public hostname: the name users reach a
service by is a decision, not a property of the host. There is no
`${TARGET_HOSTNAME}` or `${TARGET_FQDN}`: no file needs them, and an unknown
placeholder stops the install.

The host's record is `~/.config/todo/target-values.json`. A successful install
writes the public hostnames it used there (never the address, which belongs to
the host and is given each time). A later install or update without the
options therefore keeps the names instead of going back to the bundle's
defaults. `uninstall --remove-data` removes the record.

All values are resolved, checked and filled into every file in memory before
anything on the host changes. A missing or invalid value, a placeholder the
installer does not know, or a path in `bundle.json` that is absolute or leaves
the bundle stops the install with nothing written and no service touched. The
files are then installed through the same staging as before: each file is
compared and replaced atomically with its usual permissions, and only the
services whose files or images changed restart. Repeating an install with the
same values changes nothing; a new public hostname rewrites the files that
hold it and restarts the Todo and Notes databases and apps, Keycloak and the
proxy.

### Primary and standby

The DR tools install the same files from the operations package, filled in by
the same `target_render` on each host. The public hostnames are the same on
both hosts; the address is each host's own (its inventory address):

- The primary is installed with `install.sh` as above and records its
  hostnames. Publishing its databases for replication installs the replicated
  database units with its own address and keeps its recorded hostnames.
- When app-ops sets up a standby (`standby` and `rebuild`), it reads the
  primary's hostnames (`app_dr_host target-values`) and passes them to the
  standby (`--target-values`), which installs its database units with them and
  its own address and records them.
- After a failover, the promoted host's deploy installs the apps, Keycloak and
  nginx with its recorded hostnames and its own address, and every check
  (`wait-ready.sh`, the public reads, the issuer, the login page) and the
  report's next step use those names. Users keep the names they had; only the
  address they resolve to changes.

### Older bundles

A bundle without `bundle.json` was built before the files were pre-rendered
and needed Jinja2 on the target. This installer refuses it with
`... has no bundle.json: it was built in an older format ...`, before anything
changes, as it refuses a `bundle.json` of another format version (this
installer reads version 3). Build a new
bundle with `deploy/offline/build-bundle.sh`; an older bundle can still be
installed with the installer it was shipped with, which is inside it.

The installer verifies every bundled file, runs the same preflight
automatically, loads missing container images and invokes the shared Python
installer directly. On the first installation it generates every database
password and the initial Keycloak administrator password as Podman secrets,
and keeps them on later runs. No secret is stored in the bundle.

### Oracle Linux 9 with fapolicyd

Install OS-managed Python before disconnecting the target (add
`python3-pyyaml openssl` on a host that will run DR):

```bash
sudo dnf install -y python3
```

After verifying the external archive checksum from a trusted source and
extracting it, register only the installer Python files. From the bundle root:

```bash
for source in "$PWD"/deploy/installer/app_installer/*.py; do
  source=$(realpath "$source")
  sudo fapolicyd-cli --file update "$source" --trust-file app-installer ||
    sudo fapolicyd-cli --file add "$source" --trust-file app-installer
done
sudo fapolicyd-cli --update
```

Trust records must match the current resolved path, size and SHA-256 before
running Python. Refresh them after replacing a bundle; never trust an entire
home or temporary directory. SELinux and fapolicyd remain enabled. app-ops
automates this for the DR operations with the same exact-file trust.

See [FAPOLICYD.md](FAPOLICYD.md) for denial diagnostics, the difference
between `add` and `update`, common symptoms and cleanup. Do not disable
`fapolicyd` or trust the complete extracted bundle.

If existing Todo containers are found, preflight skips the clean-target port
check so the same bundle can be rerun idempotently.

`SHA256SUMS` detects changed contents, but is not a publisher signature. Anyone
able to replace both the archive and checksum file could create matching
checksums. For real distribution, sign the archive or manifest separately with
an organizational GPG or Sigstore/cosign identity and verify that signature on
the target before running `install.sh`.

### Nightly backups

Every install turns on `todo-backup.timer`: each night at 02:30 (or at the
next start, if the host was off) it takes a verified base backup of every
database into its backup volume and deletes those older than 7 days, never
the latest. A failed backup, or less than 10 % free disk, leaves
`todo-backup.service` failed; see `systemctl --user --failed` and
`journalctl --user -u todo-backup.service`. Run one now, or list them:

```bash
systemctl --user start todo-backup.service
podman exec todo-postgres ls /var/lib/postgresql/backup/base
```

The timer runs the installer from this bundle's directory (its trusted files
under fapolicyd), so keep the bundle in place, or install again from the new
one after replacing it. To put every database back to its latest backup,
which loses everything written since then, run from the bundle root:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer backup restore --confirm-restore "$(hostname)"
```

It checks every backup first, then stops the stack, replaces each data volume
with its backup and starts everything again. A single host has no WAL archive,
so it restores to the last night, not to a point in between; DR adds that. The
backups are on the same VM: copy the backup volumes elsewhere if losing the
VM must not lose the data. `install.sh` refuses to restore on a DR host, which
has its own tools.

Uninstall this offline bundle while preserving database data. The installer
refuses replication, promotion and backup hosts:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer uninstall
```

Use `--remove-data` only when permanently deleting the single-host database and
its credentials is intended. Uninstall turns the nightly backup timer off;
backup data is never removed by this command.

## Source and runtime contract

The bundle contains seven OCI archives, the target files described above (ten YAML files and
seven units for seven pods, plus the network, and the replicated database units), `bundle.json`,
the same YAML rendered with the default hostnames for reading, and the portable Python installer
with the canonical Quadlet templates.
Rendering happens only on the build host, from the shared `deploy/manifests/*.yaml.j2` and
`deploy/quadlet/*.kube.j2` templates. The source checkout's `deploy/runtime`
contains guides; package YAML is rendered fresh from the templates at build time. Packaging tests compare it to independent rendering.

The operations package contains app-ops, the DR host tools, the same Python
installer and the same target files and `bundle.json` as the bundle, which the DR tools
install; it contains no OCI archives. Both packages record the full Git SHA
and clean/dirty state in `VERSION`, checksum every file in `SHA256SUMS`, and
supply an external archive checksum. Verify the archive before extraction and
run `sha256sum -c SHA256SUMS` inside each extracted package.
