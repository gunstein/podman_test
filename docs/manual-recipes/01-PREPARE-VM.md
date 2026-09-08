# Prepare an Oracle Linux 9 VM

**Precondition:** you have created the VM in Proxmox, installed Oracle Linux 9
and have a user with sudo access. Do this preparation while the VM still has
access to the Oracle Linux package repositories.

## 1. Install required OS packages

```bash
sudo dnf update -y

sudo dnf install -y \
  podman \
  ansible-core \
  python3 \
  shadow-utils \
  fuse-overlayfs \
  slirp4netns \
  tar gzip \
  coreutils \
  openssl \
  curl \
  firewalld \
  fapolicyd

sudo systemctl enable --now fapolicyd
sudo reboot
```

`fapolicyd` is optional for the application itself (the installer detects and
adapts if it is absent), but it is part of the tested lab baseline and several
DR/backup playbooks install exact-file trust through it. Installing and
enabling it now avoids a behavior difference later.

After reboot, check versions:

```bash
podman --version
systemctl --version | head -1
ansible-playbook --version | head -1
```

The repository's current Kube runtime is tested against Podman 5.8.2, systemd
255 and ansible-core 2.14.18 or newer (see `offline/README.md`). If your
packages are older, get compatible versions before continuing.

## 2. Create a dedicated service user

Use, for example, `todo`. Skip this if you already have a regular user you
want to use instead.

```bash
sudo useradd -m -s /bin/bash todo
sudo passwd todo
```

Enable lingering so the user's systemd services can start at boot without the
user being logged in:

```bash
sudo loginctl enable-linger todo
```

## 3. Check rootless UID/GID ranges

```bash
grep '^todo:' /etc/subuid
grep '^todo:' /etc/subgid
```

Modern `useradd` on Oracle Linux 9 normally allocates these automatically; the
commands above should already show a range. Only add one manually if the user
is missing a range entirely, using free, non-overlapping values, for example:

```bash
sudo usermod --add-subuids 100000-165535 todo
sudo usermod --add-subgids 100000-165535 todo
```

Do not add these if the user already has valid ranges. Then log out and back
in as `todo`, preferably over SSH, so the user gets a proper systemd session.

## 4. Verify Podman, systemd and SELinux

Run as `todo`, without sudo:

```bash
getenforce
loginctl show-user todo -p Linger

podman info --format \
  'Rootless={{.Host.Security.Rootless}} GraphRoot={{.Store.GraphRoot}}'

systemctl --user status
```

Expected: `Enforcing`, `Linger=yes`, `Rootless=true` and a working user systemd
session. Do not disable SELinux.

## 5. Prepare the firewall

Make sure SSH is allowed before you change the firewall. If the application
should be reachable from your laptop, open only the HTTPS port from the
laptop's IP.

Example with VM `192.168.1.50` and laptop `192.168.1.10`:

```bash
sudo systemctl enable --now firewalld
sudo firewall-cmd --get-active-zones
```

Use the zone tied to the VM's network interface. The example below uses
`public`:

```bash
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family="ipv4" source address="192.168.1.10/32" port port="8443" protocol="tcp" accept'

sudo firewall-cmd --reload
```

Also check that any Proxmox-level firewall for this VM allows SSH and HTTPS —
a VM-level firewall in Proxmox is independent of `firewalld` inside the guest
and is not reset by a disk snapshot rollback. From the Proxmox node Shell:

```bash
pvesh get /nodes/localhost/qemu/100/firewall/options
```

Substitute this VM's real Proxmox VMID for `100`.

`enable: 0` means no extra restriction is in effect at that layer. Do not open
PostgreSQL or internal health ports to the network.

## 6. The VM is ready

The VM should now have working rootless Podman, the right OS packages,
SELinux enforcing, lingering and the necessary network access. You can now
remove internet access and move on to the offline installation.
