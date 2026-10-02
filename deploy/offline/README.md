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
`python3-jinja2` and `python3-pyyaml`: the DR tools render their database and
application units on the host from `deploy/quadlet`, and the replication commands parse the canonical PVC YAML
for standby bootstrap, promotion and rebuild. It also needs `openssl`, which
issues the certificates that encrypt replication.
[Prepare an Oracle Linux 9 VM](../../docs/manual-recipes/01-PREPARE-VM.md)
installs both packages on every target so this does not need revisiting later.

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
| `generated/target/manifests/` | Every Kube YAML file, with `${TARGET_EXTERNAL_HOSTNAME}` where the public hostname goes |
| `generated/target/quadlet/` | Every `.kube` unit and `app-network.network`; the proxy unit also publishes HTTPS on `${TARGET_PUBLISH_ADDRESS}` |
| `generated/target/quadlet/local-only/` | The proxy unit for a host that publishes only on 127.0.0.1 |
| `bundle.json` | Format and version (`todo-offline-bundle`, 2), where each of the above is, the apps, the HTTPS port and the default target values |
| `generated/kube-runtime/` | The Kube YAML with the build's hostname, which the DR tools install |

The build checks that putting the default hostname into the target manifests
gives exactly the normal render, so a placeholder only stands where the
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
add `--target-external-hostname NAME` for a public hostname other than the
bundle's default (see below).
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
| `${TARGET_EXTERNAL_HOSTNAME}` | The public hostname of the Todo app and of Keycloak: nginx `server_name`, the TLS certificate, the OIDC issuer, `KC_HOSTNAME` and the Keycloak client's redirect URL | `--target-external-hostname`, then the environment variable `TARGET_EXTERNAL_HOSTNAME`, then the bundle's default (`runtime.publicHostname` in the build's `values.yaml`, `todo.test`) | A DNS name: lowercase labels of letters, digits and inner hyphens |
| `${TARGET_PUBLISH_ADDRESS}` | The host IPv4 address nginx publishes HTTPS on | `--publish-address` (default `127.0.0.1`, which selects the local-only proxy unit) | A host IPv4 address, not a wildcard, multicast or reserved one |

The machine's own hostname or FQDN is never used as the public hostname: the
name users reach a service by is a decision, not a property of the host. Notes
keeps its registry hostname (`notes.test`). There is no `${TARGET_HOSTNAME}` or
`${TARGET_FQDN}`: no file needs them, and an unknown placeholder stops the
install.

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

A target-value install is for a single host. The DR tools still install
`generated/kube-runtime`, rendered with the bundle's default hostname, so set
up DR only with that hostname.

### Older bundles

A bundle without `bundle.json` was built before the files were pre-rendered
and needed Jinja2 on the target. This installer refuses it with
`... has no bundle.json: it was built in an older format ...`, before anything
changes, as it refuses a `bundle.json` of another format version. Build a new
bundle with `deploy/offline/build-bundle.sh`; an older bundle can still be
installed with the installer it was shipped with, which is inside it.

The installer verifies every bundled file, runs the same preflight
automatically, loads missing container images and invokes the shared Python
installer directly. On the first installation it generates every database
password and the initial Keycloak administrator password as Podman secrets,
and keeps them on later runs. No secret is stored in the bundle.

### Oracle Linux 9 with fapolicyd

Install OS-managed Python before disconnecting the target (add
`python3-jinja2 python3-pyyaml` on a host that will run DR):

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

Uninstall this offline bundle while preserving database data. The installer
refuses replication, promotion and backup hosts:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer uninstall
```

Use `--remove-data` only when permanently deleting the single-host database and
its credentials is intended. Backup data is never removed by this command.

## Source and runtime contract

The bundle contains seven OCI archives, the target files described above (ten YAML files and
seven units for seven pods, plus the network), `bundle.json`, the same YAML rendered for DR,
and the portable Python installer with the canonical Quadlet templates the DR tools use.
Rendering happens only on the build host, from the shared `deploy/manifests/*.yaml.j2` and
`deploy/quadlet/*.kube.j2` templates. The source checkout's `deploy/runtime`
contains guides; package YAML is fresh Jinja2 output. Packaging tests compare it to independent rendering.

The operations package contains complete DR/backup roles, task includes, the same Python
installer, runtime manifests and shared resource Quadlets; it contains no OCI archives. Both packages record the full Git SHA
and clean/dirty state in `VERSION`, checksum every file in `SHA256SUMS`, and
supply an external archive checksum. Verify the archive before extraction and
run `sha256sum -c SHA256SUMS` inside each extracted package.
