# M13 PostgreSQL replication bootstrap

Run the read-only preflight before changing either database host:

```bash
ansible-playbook --inventory ansible/inventory-m13.ini ansible/preflight-m13.yml
```

By default, the M12 offline bundle must still exist on standby under
`/home/<ansible_user>/todo-offline-m12`. Set `todo_user_home` in the inventory
when the remote account uses another home directory.

Configure the primary host firewall before bootstrap publishes PostgreSQL on
the LAN interface. Allow only standby and reload firewalld:

```bash
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="<standby-address>/32" destination address="<primary-address>" port port="5432" protocol="tcp" accept'
sudo firewall-cmd --reload
```

Do not add a general PostgreSQL service or open TCP 5432 to the entire LAN.
Rootless Podman port forwarding does not preserve the original client source
address. The primary role therefore inspects `todo-network` and grants the
dedicated replication role access from that internal Podman subnet. In the
verified Oracle Linux environment PostgreSQL saw `10.89.0.0/24`, not the
standby LAN address; firewalld enforces the real machine boundary.

M13 authenticates replication with SCRAM-SHA-256 but does not configure or
require encrypted PostgreSQL transport. It is intended for this isolated,
trusted demo LAN. A networked deployment should add PostgreSQL TLS with
`hostssl` and `sslmode=verify-full`, or use a separately protected replication
network, before treating WAL traffic as confidential.

The initial bootstrap is deliberately separate from normal deployment. It
creates the replication credential and role, publishes primary PostgreSQL,
takes one streamed base backup and starts standby in recovery mode:

```bash
ansible-playbook --inventory ansible/inventory-m13.ini ansible/bootstrap-m13.yml
```

The bootstrap verifies connectivity before creating the standby volume.

`bootstrap-m13.yml` is a one-time operation and refuses to overwrite an
existing standby volume. If it fails after creating the volume or physical slot,
do not rerun it blindly: inspect the partial state first. Dropping the slot or
deleting the volume is an explicit destructive recovery operation, never an
automatic playbook cleanup. After bootstrap, inspect replication with:

```bash
ansible-playbook --inventory ansible/inventory-m13.ini ansible/status-m13.yml
```

The expected primary state is `streaming|async`; standby must report recovery
as `t`. Status also requires an active, usable `todo_standby` slot and reports
its WAL status, remaining safe WAL bytes and any invalidation reason.

A new bootstrap also installs the M13.5 DR tool. For an already bootstrapped
standby, install or update it without touching the database:

```bash
ansible-playbook --inventory ansible/inventory-m13.ini ansible/install-dr-m13.yml
```

See [the controlled promotion runbook](M13.5-PROMOTION.md) before using it.

The Oracle Linux 9.8 verification also required `0700` on the basebackup
directory, an explicit `:Z` SELinux label on its Quadlet volume mount and an
explicit `/bin/sh` entrypoint for the readable standby startup script.

A physical slot retains WAL while standby is disconnected, capped at
1 GiB to protect primary disk space. Exceeding the cap may invalidate the slot
and require an explicit standby rebuild. The standby PostgreSQL Quadlet is
attached to the user `default.target`; enable lingering for the service user when
it must restart at VM boot without an interactive login.

A VM cloned from a host with existing rootless Podman storage can inherit stale
lock allocation metadata. If Podman reports `Refreshing volume .* acquiring lock
.* file exists`, stop all user Podman services and processes and run
`podman system renumber` once. This does not delete the database volume; do not
use volume deletion as a lock repair.
