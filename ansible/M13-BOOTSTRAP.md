# M13 PostgreSQL replication bootstrap

Run the read-only preflight before changing either database host:

```bash
ansible-playbook --inventory ansible/inventory-m13.ini ansible/preflight-m13.yml
```

The initial bootstrap is deliberately separate from normal deployment. It
creates a dedicated replication credential and database role, exposes primary
PostgreSQL on the primary LAN address, takes one streamed base backup and starts
standby in recovery mode:

```bash
ansible-playbook --inventory ansible/inventory-m13.ini ansible/bootstrap-m13.yml
```

By default, the M12 offline bundle must still exist on standby under
`/home/<ansible_user>/todo-offline-m12`. Set `todo_user_home` in the inventory
when the remote account uses another home directory.

Rootless Podman's port proxy does not preserve the original client source address.
The primary role therefore inspects `todo-network` and grants the dedicated
replication role access from that internal Podman subnet. In the verified Oracle
Linux environment PostgreSQL saw the connection from `10.89.0.0/24`, not from
the standby LAN address. The host firewall must enforce the real machine boundary.
On primary, allow only standby and reload firewalld:

```bash
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="<standby-address>/32" destination address="<primary-address>" port port="5432" protocol="tcp" accept'
sudo firewall-cmd --reload
```

Do not add a general PostgreSQL service or open TCP 5432 to the entire LAN. The
bootstrap verifies connectivity before creating the standby volume.

`bootstrap-m13.yml` is a one-time operation and refuses to overwrite an
existing standby volume. If it fails after creating the volume or physical slot,
do not rerun it blindly: inspect the partial state first. Dropping the slot or
deleting the volume is an explicit destructive recovery operation, never an
automatic playbook cleanup. After bootstrap, inspect replication with:

```bash
ansible-playbook --inventory ansible/inventory-m13.ini ansible/status-m13.yml
```

The expected primary state is `streaming|async`; standby must report recovery
as `t`. The Oracle Linux 9.8 verification also required `0700` on the basebackup
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
