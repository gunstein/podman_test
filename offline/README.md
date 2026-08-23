# Offline bundle

The bundle installs the Todo application without contacting a container registry or Python package index. The target machine must already provide rootless Podman, Python 3 with `venv`, user systemd, and standard tools including `sha256sum` and `tar`.

## Build on the connected machine

From the project root:

```bash
offline/build-bundle.sh
```

This builds the application images, pulls PostgreSQL, downloads the pinned Ansible wheels and creates:

```text
dist/todo-offline-m9.tar.gz
```

The downloaded wheels are platform-specific. Build the bundle on a machine compatible with the offline target.

## Install on the offline machine

Copy only the archive to the target, then run:

```bash
tar -xzf todo-offline-m9.tar.gz
cd todo-offline-m9
./install.sh
```

The installer verifies every bundled file, creates an isolated Ansible virtualenv from local wheels, loads missing container images and runs the same deployment playbook as the online installation.

Uninstall while preserving database data:

```bash
.venv/bin/ansible-playbook --inventory ansible/inventory.ini ansible/uninstall.yml
```
