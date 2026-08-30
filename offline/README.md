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
- RPM/deb-managed `ansible-core` 2.14 or newer and its system Python
- `/bin/sh`, `tar` and `sha256sum`
- Free localhost ports 5432, 8000, 8080 and 8443 on a clean target

The tested baseline is Podman 4.9.3, systemd 255 and ansible-core 2.14.18. The
bundle must be built on a machine compatible with the target's CPU architecture.

For a comfortable demo VM, provide at least 4 GiB memory and 10 GiB free disk.
The preflight script reports available resources but treats these figures as
recommendations rather than hard requirements.

## Build on the connected machine

From the project root:

```bash
offline/build-bundle.sh
```

This builds the backend, frontend and Keycloak images, pulls PostgreSQL, and
creates both the archive and its external checksum:

```text
dist/todo-offline-m12.tar.gz
dist/todo-offline-m12.tar.gz.sha256
```

Build the bundle on a machine compatible with the offline target.

## Install on the offline machine

Copy the archive and checksum to the target through the trusted transfer path.
Verify the archive before extracting or running any bundled code:

```bash
sha256sum -c todo-offline-m12.tar.gz.sha256
tar -xzf todo-offline-m12.tar.gz
cd todo-offline-m12
sh ./preflight.sh
sh ./install.sh
```

Running the scripts through the trusted system shell is intentional. On a
machine with active `fapolicyd`, newly extracted scripts cannot yet be executed
directly with `./script.sh`. The RPM-managed shell reads them as data. The
installer does not add the extracted bundle to the trust database.

The preflight script does not change host configuration. It verifies that the
host-managed Ansible and Python are present.

The installer verifies every bundled file, runs the same preflight
automatically, loads missing container images and runs the same deployment
playbook with the host's Ansible. On the first installation it asks
for the database password and an initial Keycloak administrator password.
Neither secret is stored in the bundle.

### Oracle Linux 9 with fapolicyd

Install Ansible from Oracle Linux AppStream before disconnecting the target:

```bash
sudo dnf install -y ansible-core
```

The RPM installation makes Ansible and its Python dependencies trusted through
the normal package database. The bundle therefore needs no custom `fapolicyd`
rules or trust entries. Ansible pipelining avoids executing transient modules
from `~/.ansible/tmp`. SELinux and `fapolicyd` remain enabled.

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

Uninstall this M12 bundle while preserving database data. The playbook refuses
to run if it detects later replication, promotion or backup state:

```bash
ANSIBLE_PIPELINING=true ansible-playbook \
  --inventory ansible/inventory.ini ansible/uninstall.yml
```
