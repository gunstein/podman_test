# Offline bundle

The bundle installs the Todo application without contacting a container
registry or Python package index. It does not install operating-system
prerequisites.

## Target prerequisites

The target machine must already provide:

- Podman configured for the current non-root user
- Rootless user namespaces, normally backed by entries in `/etc/subuid` and
  `/etc/subgid`
- Podman's Quadlet systemd generator
- A working `systemctl --user` session
- OS-managed Python 3.9+ and Jinja2 (for example `python3-jinja2`)
- `/bin/sh`, `tar` and `sha256sum`
- Free host ports 5432, 8080 and 8443 on a clean target (8000 is internal to the app pod)

The Kube runtime requires the tested Podman 5.8.2 platform, systemd 255 and
Python/Jinja2. Ansible is required separately for DR operations. Rendering is
not an offline target dependency. The
bundle must be built on a machine compatible with the target's CPU architecture.

For a comfortable demo VM, provide at least 4 GiB memory and 10 GiB free disk.
The preflight script reports available resources but treats these figures as
recommendations rather than hard requirements.

## Build on the connected machine

From the project root:

```bash
deploy/offline/build-bundle.sh
```

The connected build machine renders the production
values with Jinja2 before packaging; the isolated Oracle Linux target receives plain YAML.

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

For a separate lab client, use `sh ./install.sh --publish-address 192.168.0.102`.
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

The preflight script does not change host configuration. It verifies that the
host-managed Python and Jinja2 are present.

The installer verifies every bundled file, runs the same preflight
automatically, loads missing container images and invokes the shared Python
installer directly. No Ansible process is needed for single-host installation. On the first installation it asks
for the database password and an initial Keycloak administrator password.
Neither secret is stored in the bundle.

### Oracle Linux 9 with fapolicyd

Install OS-managed Python and Jinja2 before disconnecting the target:

```bash
sudo dnf install -y python3 python3-jinja2
```

After verifying the external archive checksum from a trusted source and
extracting it, register only the installer Python files. From the bundle root:

```bash
for source in "$PWD"/deploy/installer/todo_installer/*.py; do
  source=$(realpath "$source")
  sudo fapolicyd-cli --file update "$source" --trust-file todo-installer ||
    sudo fapolicyd-cli --file add "$source" --trust-file todo-installer
done
sudo fapolicyd-cli --update
```

Trust records must match the current resolved path, size and SHA-256 before
running Python. Refresh them after replacing a bundle; never trust an entire
home or temporary directory. SELinux and fapolicyd remain enabled. DR Ansible
operations automate this through the existing exact-file trust role instead.

See [FAPOLICYD.md](FAPOLICYD.md) for denial diagnostics, the difference
between `add` and `update`, common Ansible symptoms and cleanup. Do not disable
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
PYTHONPATH=deploy/installer python3 -m todo_installer uninstall
```

Use `--remove-data` only when permanently deleting the single-host database and
its credentials is intended. Backup data is never removed by this command.

## Source and runtime contract

The bundle contains seven OCI archives, ten YAML files for six pods, and the portable Python
installer with the canonical target Quadlet templates. Rendering happens only on the build
host, from the shared `deploy/manifests/*.yaml.j2` templates. The source checkout's `deploy/runtime`
contains guides; package YAML is fresh Jinja2 output. Packaging tests compare it to independent rendering.

The operations package contains complete DR/backup roles, task includes, the same Python
installer, runtime manifests and shared resource Quadlets; it contains no OCI archives. Both packages record the full Git SHA
and clean/dirty state in `VERSION`, checksum every file in `SHA256SUMS`, and
supply an external archive checksum. Verify the archive before extraction and
run `sha256sum -c SHA256SUMS` inside each extracted package.
