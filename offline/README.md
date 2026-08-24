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
- The same Python major/minor version used to build the bundle
- `/bin/sh`, `tar` and `sha256sum`
- Free localhost ports 5432, 8000, 8080 and 8443 on a clean target

The tested baseline is Podman 4.9.3, systemd 255 and Python 3.12.3. The bundle
records its Python major/minor version in `PYTHON_VERSION` and refuses a
different target interpreter. It must also be built on a machine compatible
with the target's CPU architecture, operating system and Python wheel platform.

For a comfortable demo VM, provide at least 4 GiB memory and 10 GiB free disk.
The preflight script reports available resources but treats these figures as
recommendations rather than hard requirements.

## Build on the connected machine

From the project root:

```bash
offline/build-bundle.sh
```

This builds the backend, frontend and Keycloak images, pulls PostgreSQL,
downloads the pinned Ansible wheels, prepares a self-contained Ansible runtime
and creates:

```text
dist/todo-offline-m12.tar.gz
```

The downloaded wheels are platform-specific. Build the bundle on a machine compatible with the offline target.

## Install on the offline machine

Copy only the archive to the target, then run:

```bash
tar -xzf todo-offline-m12.tar.gz
cd todo-offline-m12
sh ./preflight.sh
sh ./install.sh
```

Running the scripts through the trusted system shell is intentional. On a
machine with active `fapolicyd`, newly extracted scripts cannot yet be executed
directly with `./script.sh`. The shell may read them as data, and the installer
then registers the files it needs to execute as trusted.

The preflight script does not change host configuration. It verifies the exact
Python major/minor required by the bundled Ansible runtime. The target does not
need `pip` or `venv`.

The installer verifies every bundled file, runs the same preflight
automatically, loads missing container images and runs the same deployment
playbook with the bundled Ansible runtime. On the first installation it asks
for the database password and an initial Keycloak administrator password.
Neither secret is stored in the bundle.

### Oracle Linux 9 with fapolicyd

Install Python 3.12 before disconnecting the target:

```bash
sudo dnf install -y python3.12
```

When `fapolicyd` is active, `install.sh` asks for sudo approval to register the
prebuilt `ansible-runtime` in `/etc/fapolicyd/trust.d/todo-offline`. Nothing is
installed with `pip` on the target. Bytecode generation is disabled so trusted
files are not followed by new untrusted `.pyc` files. Ansible pipelining avoids
executing transient modules from `~/.ansible/tmp`. SELinux and `fapolicyd`
remain enabled.

The runtime is registered before `SHA256SUMS` is checked because a restrictive
`fapolicyd` policy may prevent `sha256sum` from reading untrusted native Python
libraries. The installer removes that runtime trust entry immediately if any
bundle checksum fails.

To remove these trust entries after permanently uninstalling the demo:

```bash
sudo rm -f /etc/fapolicyd/trust.d/todo-offline
sudo fapolicyd-cli --update
```

If existing Todo containers are found, preflight skips the clean-target port
check so the same bundle can be rerun idempotently.

`SHA256SUMS` detects changed contents, but is not a publisher signature. Anyone
able to replace both the archive and checksum file could create matching
checksums. For real distribution, sign the archive or manifest separately with
an organizational GPG or Sigstore/cosign identity and verify that signature on
the target before running `install.sh`.

Uninstall while preserving database data:

```bash
PYTHONPATH=ansible-runtime PYTHONDONTWRITEBYTECODE=1 \
  python3.12 -m ansible.cli.playbook \
  --inventory ansible/inventory.ini ansible/uninstall.yml
```
