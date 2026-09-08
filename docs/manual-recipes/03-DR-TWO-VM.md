# Two-VM DR walkthrough

The simplest complete path through the DR functionality on
`feature/podman-kube`: bootstrap a standby, simulate losing the primary,
promote the standby, and optionally rebuild the old primary as a new standby.

```text
Laptop          = build machine / client
todo-primary    = 192.168.1.50   (VM1, Proxmox VMID 100)
todo-standby    = 192.168.1.51   (VM2, Proxmox VMID 101)
service user    = todo
```

Substitute your own addresses and Proxmox VMIDs throughout; `100`/`101` below
are examples.

Normal state:

```text
todo.test
    |
    v
VM1  todo-primary
     App + Keycloak + PostgreSQL primary
                       |
                       | async replication
                       v
VM2  todo-standby      PostgreSQL standby
```

After DR:

```text
todo.test
    |
    v
VM2  todo-standby
     App + Keycloak + PostgreSQL primary
```

VM1 is then rebuilt as the new standby.

See [How these differ from the reviewed procedures](README.md#how-these-differ-from-the-reviewed-procedures)
before running this on anything other than disposable lab VMs.

## 1. Prepare both VMs

Use [Prepare an Oracle Linux 9 VM](01-PREPARE-VM.md) on both.

Also set distinct hostnames — several playbooks assert that the actual
hostname matches the inventory entry, so this is not just cosmetic:

```bash
# VM1
sudo hostnamectl set-hostname todo-primary

# VM2
sudo hostnamectl set-hostname todo-standby
```

Both should show:

```bash
getenforce
podman --version
ansible-playbook --version | head -1
loginctl show-user todo -p Linger
```

SELinux should be `Enforcing`, and rootless Podman/user systemd should work.

## 2. Build both packages on the laptop

From the repository:

```bash
git switch feature/podman-kube
git status --short
git rev-parse HEAD
```

Build the application bundle (see
[Offline install](02-OFFLINE-INSTALL.md#1-build-the-offline-bundle-on-the-laptop)
for the Helm prerequisite):

```bash
offline/build-bundle.sh
```

Build the DR/operations package:

```bash
scripts/build-operations-package.sh
```

Check:

```bash
cd dist

sha256sum -c todo-offline-m12.tar.gz.sha256
sha256sum -c todo-operations.tar.gz.sha256
```

You now have:

```text
todo-offline-m12.tar.gz
todo-offline-m12.tar.gz.sha256

todo-operations.tar.gz
todo-operations.tar.gz.sha256
```

The operations package contains the Ansible and DR tools; the offline bundle
contains the container images.

## 3. Copy the offline bundle to BOTH VMs

This matters. VM2 needs the bundle later, during standby bootstrap and
application failover.

From the laptop:

```bash
scp todo-offline-m12.tar.gz todo-offline-m12.tar.gz.sha256 \
    todo@192.168.1.50:

scp todo-offline-m12.tar.gz todo-offline-m12.tar.gz.sha256 \
    todo@192.168.1.51:
```

On both VMs:

```bash
cd ~

sha256sum -c todo-offline-m12.tar.gz.sha256
tar -xzf todo-offline-m12.tar.gz
```

Do not run `install.sh` on VM2. The bundle should just sit at
`/home/todo/todo-offline-m12`. Standby bootstrap expects this bundle and
loads the PostgreSQL image from it.

## 4. Install Todo normally on VM1

This is [Offline install on one VM](02-OFFLINE-INSTALL.md).

On VM1:

```bash
cd ~/todo-offline-m12

sh ./preflight.sh
sh ./install.sh --publish-address 192.168.1.50
```

Check:

```bash
systemctl --user is-active \
  todo-postgres.service \
  todo-keycloak.service \
  todo-app.service

curl --fail http://127.0.0.1:8080/ready
```

From the laptop, `todo.test` should resolve to `192.168.1.50`, and Todo should
work normally.

Create a Todo now named:

```text
DR test before failover
```

You will use it later to confirm the data survived.

## 5. Copy the operations package to both VMs

From the laptop:

```bash
scp todo-operations.tar.gz todo-operations.tar.gz.sha256 \
    todo@192.168.1.50:

scp todo-operations.tar.gz todo-operations.tar.gz.sha256 \
    todo@192.168.1.51:
```

On both:

```bash
cd ~

sha256sum -c todo-operations.tar.gz.sha256
tar -xzf todo-operations.tar.gz
```

You should now have `~/todo-operations` on both machines.

## The DR setup itself

## 6. Set up SSH from VM1 to VM2

VM1 acts as the Ansible controller during normal operation.

On VM1, as `todo`:

```bash
test -f ~/.ssh/id_rsa || ssh-keygen \
  -t rsa -b 3072 -N '' \
  -C 'todo-primary-to-standby' \
  -f ~/.ssh/id_rsa

ssh-copy-id todo@192.168.1.51

ssh -o BatchMode=yes todo@192.168.1.51 hostname
```

This should return `todo-standby` without asking for a password.

To make the later rebuild easier, also set up the reverse direction now,
VM2 → VM1:

```bash
# Run on VM2

test -f ~/.ssh/id_rsa || ssh-keygen \
  -t rsa -b 3072 -N '' \
  -C 'todo-standby-to-primary' \
  -f ~/.ssh/id_rsa

ssh-copy-id todo@192.168.1.50

ssh -o BatchMode=yes todo@192.168.1.50 hostname
```

## 7. Build the inventory on VM1

On VM1:

```bash
cd ~/todo-operations

cp ansible/inventory-initial.example.ini \
   ansible/inventory-initial.ini
```

Replace the addresses and user:

```bash
sed -i \
  -e 's/192\.0\.2\.10/192.168.1.50/g' \
  -e 's/192\.0\.2\.11/192.168.1.51/g' \
  -e 's/ansible_user=gunstein/ansible_user=todo/' \
  ansible/inventory-initial.ini
```

Check:

```bash
cat ansible/inventory-initial.ini
```

Test the inventory:

```bash
ansible-inventory \
  --inventory ansible/inventory-initial.ini \
  --graph
```

Test both machines:

```bash
ansible \
  --inventory ansible/inventory-initial.ini \
  todo_cluster \
  -m ping
```

The inventory model is VM1 as the local primary and VM2 as the remote
standby.

## 8. Open PostgreSQL only between VM2 and VM1

On VM1:

```bash
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family="ipv4" source address="192.168.1.51/32" destination address="192.168.1.50" port port="5432" protocol="tcp" accept'

sudo firewall-cmd --reload
```

Do not open PostgreSQL generally to the LAN. Only VM2 should be able to reach
VM1 on 5432.

## 9. Run standby preflight

Still on VM1:

```bash
cd ~/todo-operations

ansible-playbook \
  --inventory ansible/inventory-initial.ini \
  ansible/preflight-standby.yml
```

This should be green before you continue.

## 10. Build the PostgreSQL standby on VM2

On VM1:

```bash
ansible-playbook \
  --inventory ansible/inventory-initial.ini \
  ansible/bootstrap-standby.yml
```

This, among other things:

```text
VM1 PostgreSQL
   |
   +-- creates the replication role
   +-- creates the replication slot
   +-- synchronizes secrets
   |
   +---- pg_basebackup ----> VM2
                              |
                              v
                         PostgreSQL standby
```

Secrets for the database, app, Keycloak and replication are synchronized to
VM2 without plaintext files.

Do not blindly rerun bootstrap if it fails partway. It is designed as a
one-shot operation; see
[Acceptance troubleshooting](../ACCEPTANCE-TROUBLESHOOTING.md) if it fails.

## 11. Check replication

On VM1:

```bash
ansible-playbook \
  --inventory ansible/inventory-initial.ini \
  ansible/replication-status.yml
```

You want, among other things:

```text
primary: streaming|async
standby: recovery=t
```

and an active `todo_standby` replication slot.

## 12. Install the DR tool on VM2

While VM1 is still working:

```bash
ansible-playbook \
  --ask-become-pass \
  --inventory ansible/inventory-initial.ini \
  ansible/install-dr-tool.yml
```

VM2 should now have:

```text
/opt/todo/bin/todo_dr.py
```

Test from VM2:

```bash
python3 /opt/todo/bin/todo_dr.py status
```

You expect roughly:

```text
Database role: standby
Writable: no
Primary endpoint: reachable
```

This is the normal state.

## Install the quarantine tool (while VM1 is still up)

The disaster-recovery test below fences VM1. Fencing evidence must come from
the hypervisor, not from stopping services alone, and once VM1 is fenced you
can no longer SSH in to install anything. Install the quarantine tool now,
while VM1 is still reachable and healthy:

```bash
ansible-playbook \
  --ask-become-pass \
  --inventory ansible/inventory-initial.ini \
  ansible/install-quarantine-tool.yml
```

This installs one root-owned helper script on VM1 with exact `fapolicyd`
trust; it does not stop anything. See
[Proxmox quarantine](../PROXMOX-QUARANTINE.md) for the full model, including
the optional Guest Agent `guest-exec` opt-in some Proxmox/SELinux
configurations need. Step 21 below uses this tool instead of the VM console.

## The DR setup is now complete

Before you test the disaster, the state should be:

```text
VM1
  PostgreSQL PRIMARY
  Todo
  Keycloak
  nginx

       |
       | WAL replication
       v

VM2
  PostgreSQL STANDBY
  DR tool
  quarantine tool
  offline bundle
  operations package
```

Check once more:

```bash
# VM1
cd ~/todo-operations

ansible-playbook \
  --inventory ansible/inventory-initial.ini \
  ansible/replication-status.yml
```

And confirm `DR test before failover` exists in the application.

## Disaster recovery test

## 13. Simulate losing VM1

Now VM1 gets fenced.

This is more than just stopping PostgreSQL. The old primary must not be able
to come back and start writing at the same time as the new primary.

In Proxmox: stop `todo-primary` and make sure it does not restart
automatically.

From the Proxmox Shell you can check:

```bash
qm status 100
```

The result should be:

```text
status: stopped
```

The repository's promotion procedure explicitly requires the old primary to
be fenced before VM2 is promoted.

## 14. Preflight on VM2

Log in to VM2:

```bash
ssh todo@192.168.1.51
```

Run:

```bash
python3 /opt/todo/bin/todo_dr.py preflight \
  --confirm-primary-fenced 'todo-primary is fenced'
```

It checks, among other things, that:

```text
local PostgreSQL is standby
local database is healthy
apply lag = 0
VM1:5432 is unreachable
```

If preflight fails: do not promote.

## 15. Promote VM2 to PostgreSQL primary

On VM2:

```bash
python3 /opt/todo/bin/todo_dr.py promote \
  --confirm-primary-fenced 'todo-primary is fenced' \
  --confirm-promotion todo-standby
```

Check:

```bash
python3 /opt/todo/bin/todo_dr.py status
```

And directly against PostgreSQL:

```bash
podman exec todo-postgres \
  psql --username todo --dbname postgres \
  --tuples-only --no-align \
  --command \
  "SELECT pg_is_in_recovery(), current_setting('transaction_read_only');"
```

Expected:

```text
f|off
```

That means: not in recovery, and writable.

## Bring the Todo application up on VM2

## 16. Build the recovery inventory on VM2

On VM2:

```bash
cd ~/todo-operations

cp ansible/inventory-recovery.example.ini \
   ansible/inventory-recovery.ini
```

Set the addresses and user:

```bash
sed -i \
  -e 's/192\.0\.2\.10/192.168.1.50/g' \
  -e 's/192\.0\.2\.11/192.168.1.51/g' \
  -e 's/ansible_user=gunstein/ansible_user=todo/' \
  -e 's#/home/gunstein#/home/todo#g' \
  ansible/inventory-recovery.ini
```

The roles now mean:

```text
todo-standby = CURRENT PRIMARY
todo-primary = old primary, to be rebuilt later
```

The machine names do not change; the inventory groups describe the roles.

## 17. Open HTTPS on VM2

On VM2, allow only the laptop.

Example if the laptop is `192.168.1.10`:

```bash
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family="ipv4" source address="192.168.1.10/32" port port="8443" protocol="tcp" accept'

sudo firewall-cmd --reload
```

## 18. Start Todo and Keycloak on VM2

On VM2:

```bash
cd ~/todo-operations

ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/deploy-promoted-application.yml
```

The playbook first checks that PostgreSQL is actually promoted and writable,
that the required secrets exist, and that container images can be loaded from
the offline bundle.

Check:

```bash
systemctl --user is-active \
  todo-postgres.service \
  todo-keycloak.service \
  todo-app.service

curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
curl --fail http://127.0.0.1:8080/api/todos
```

## 19. Move todo.test to VM2

On the laptop, `todo.test` must now point to `192.168.1.51`.

If you use `/etc/hosts`:

```bash
sudo sed -i \
  '/[[:space:]]todo\.test\([[:space:]]\|$\)/d' \
  /etc/hosts

echo '192.168.1.51 todo.test' | sudo tee -a /etc/hosts
```

VM2 creates its own demo CA the first time nginx starts, so you also need to
trust VM2's public CA on the laptop — use
`scripts/trust-serving-ca.sh todo@192.168.1.51`, or follow the
[TLS guide](../TLS.md) manually.

Open <https://todo.test:8443>.

## 20. Verify DR

This is the actual test.

Confirm that the Todo:

```text
DR test before failover
```

still exists.

Log in through Keycloak/Todo and create a new one:

```text
DR test after failover
```

If both exist and the new write works, you have demonstrated:

```text
VM1 lost
      v
VM2 promoted
      v
PostgreSQL writable
      v
Todo + Keycloak started
      v
Existing data preserved
      v
New data can be written
```

## Optional, but recommended: restore redundancy

The DR test already works on VM2, but you now only have one copy of the
database. The next step is to turn the old VM1 into the standby.

## 21. Start old VM1 in quarantine

Do not just start VM1 normally. It contains an old, diverging primary.

Start it from Proxmox with all network links disconnected first, then use the
quarantine tool installed in step 12 through Guest Agent — not the VM console
— to stop its old services:

```bash
qm start 100
# with the network link(s) disconnected
qm guest exec 100 -- /opt/todo/bin/todo-quarantine.sh stop todo-primary todo
```

Require a completed response with `exitcode: 0` and `STOPPED`. The helper
checks hostname, user and service state itself; it does not require you to
type anything inside the guest. See
[Proxmox quarantine](../PROXMOX-QUARANTINE.md) for the firewall profile,
the Guest Agent opt-ins some setups need, and what to do if the stop command
fails, times out, or returns only a PID.

Only after the stop is confirmed do you reconnect the network link (management
SSH only, per the quarantine profile). `todo-postgres` must not be running.
The repository requires this isolation because the old VM1 contains a
previously writable database.

## 22. Test SSH from VM2 to VM1

On VM2:

```bash
ssh -o BatchMode=yes todo@192.168.1.50 hostname
```

Expect:

```text
todo-primary
```

Test Ansible:

```bash
cd ~/todo-operations

ansible \
  --inventory ansible/inventory-recovery.ini \
  todo_cluster \
  -m ping
```

## 23. Allow the new replication direction

The roles are now reversed:

```text
VM2 primary -> VM1 standby
```

On VM2:

```bash
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family="ipv4" source address="192.168.1.50/32" destination address="192.168.1.51" port port="5432" protocol="tcp" accept'

sudo firewall-cmd --reload
```

## 24. Preflight before rebuilding VM1

On VM2:

```bash
cd ~/todo-operations

ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/preflight-standby-rebuild.yml \
  --extra-vars \
  '{"todo_confirm_old_primary_fenced":"todo-primary is fenced","todo_confirm_reseed":"todo-primary"}'
```

This should be green before the next step.

## 25. Rebuild old VM1 as standby

This deletes the old PostgreSQL database on VM1.

Run from VM2:

```bash
ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/rebuild-standby.yml \
  --extra-vars \
  '{"todo_confirm_old_primary_fenced":"todo-primary is fenced","todo_confirm_reseed":"todo-primary"}'
```

The playbook deletes the old `todo-postgres-data`, takes a fresh
`pg_basebackup` from VM2, and starts VM1 as a read-only standby. It also
removes the application tier from VM1, so only PostgreSQL standby runs there.
If it fails partway, do not repeat it blindly — see
[Acceptance troubleshooting](../ACCEPTANCE-TROUBLESHOOTING.md).

## 26. Final check

From VM2:

```bash
ansible-playbook \
  --inventory ansible/inventory-recovery.ini \
  ansible/cluster-status.yml
```

You should end up with:

```text
VM2 todo-standby
    PostgreSQL PRIMARY
    Todo
    Keycloak
    nginx
         |
         | async replication
         v
VM1 todo-primary
    PostgreSQL STANDBY
```

`todo.test` should still point to VM2.

This is a fully valid end state. You do not need to move the primary back to
VM1. That would be a separate, planned failback/switchover operation;
`rebuild-standby.yml` does not do this automatically.

## Short version of the whole run

```text
Recipe 1
  -> prepare VM1 + VM2

Recipe 2
  -> build offline bundle
  -> install Todo on VM1
  -> stage the same bundle on VM2

DR setup
  -> build operations package
  -> inventory
  -> bootstrap-standby.yml
  -> replication-status.yml
  -> install-dr-tool.yml
  -> install-quarantine-tool.yml

SIMULATE DISASTER
  -> fence VM1
  -> todo_dr.py preflight
  -> todo_dr.py promote
  -> deploy-promoted-application.yml
  -> todo.test -> VM2
  -> test Todo

RESTORE REDUNDANCY
  -> boot VM1 in quarantine, stop via todo-quarantine.sh
  -> preflight-standby-rebuild.yml
  -> rebuild-standby.yml
  -> cluster-status.yml
```

This is the simplest complete path through the DR functionality that actually
exists in the `feature/podman-kube` branch.
