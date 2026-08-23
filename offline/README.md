# Offline bundle

The bundle installs the Todo application without contacting a container registry or Python package index. The target machine must already provide rootless Podman, Python 3 with `venv`, user systemd, and standard tools including `sha256sum` and `tar`.

## Build on the connected machine

From the project root:

```bash
offline/build-bundle.sh
```

This builds the backend, frontend and Keycloak images, pulls PostgreSQL, downloads
the pinned Ansible wheels and creates:

```text
dist/todo-offline-m12.tar.gz
```

The downloaded wheels are platform-specific. Build the bundle on a machine compatible with the offline target.

## Install on the offline machine

Copy only the archive to the target, then run:

```bash
tar -xzf todo-offline-m12.tar.gz
cd todo-offline-m12
./install.sh
```

The installer verifies every bundled file, creates an isolated Ansible virtualenv
from local wheels, loads missing container images and runs the same deployment
playbook as the online installation. On the first installation it asks for the
database password and an initial Keycloak administrator password. Neither secret
is stored in the bundle.

`SHA256SUMS` detects changed contents, but is not a publisher signature. Anyone
able to replace both the archive and checksum file could create matching
checksums. For real distribution, sign the archive or manifest separately with
an organizational GPG or Sigstore/cosign identity and verify that signature on
the target before running `install.sh`.

Uninstall while preserving database data:

```bash
.venv/bin/ansible-playbook --inventory ansible/inventory.ini ansible/uninstall.yml
```
