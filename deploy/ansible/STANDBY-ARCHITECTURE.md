# Initial primary and standby preparation

The primary host is the Ansible controller during normal operation. The standby
must nevertheless contain everything needed for local promotion; failover must
not depend on the primary still being available.

Copy and edit the example inventory on primary:

```bash
cp deploy/ansible/inventories/initial/hosts.example.ini deploy/ansible/inventories/initial/hosts.ini
```

Replace the example standby address in `hosts.ini` and adjust `ansible_user`
in `inventories/initial/group_vars/todo_cluster.yaml` if necessary. The
primary entry deliberately uses a local connection. Test SSH with host-key
checking before running Ansible. After restoring a VM snapshot, verify the
standby's current host-key fingerprint through an independently verified
connection before accepting a new key on primary; do not use an unverified `ssh-keyscan` result as trust evidence.

The primary also needs a non-interactive user key for Ansible. Re-create and
install it if the clean snapshot predates SSH setup:

```bash
test -f "$HOME/.ssh/id_rsa" || ssh-keygen \
  -t rsa -b 3072 -N '' \
  -C 'todo-primary-to-standby' \
  -f "$HOME/.ssh/id_rsa"
ssh-copy-id -i "$HOME/.ssh/id_rsa.pub" gunstein@<standby-address>
ssh -o BatchMode=yes gunstein@<standby-address> hostname
```

`deploy/scripts/bootstrap-ssh-key.sh gunstein@<standby-address> <expected-fingerprint>`
does the same, plus it pins the host key only after checking it against the
fingerprint you supply (obtained through the independently verified connection
above) instead of a blind interactive accept. Without a fingerprint argument
it falls back to the same interactive prompt as `ssh-copy-id` alone.

The final command must return the standby hostname without asking for a
password. Keep the private key only on primary and never add it to an archive or
the repository.

Ensure the pinned PostgreSQL 17.11 image is available on both hosts. Then copy the
existing credentials from primary to standby:

```bash
ansible-playbook \
  --inventory deploy/ansible/inventories/initial/hosts.ini \
  deploy/ansible/playbooks/sync-standby-secrets.yml
```

Ansible is only the transport here. On primary, `python3 -m app_installer
export-replication-secrets` reads every credential of the complete replication
group with `podman secret inspect --showsecret` and prints them as one opaque
base64 value. Ansible keeps that value in memory with `no_log` and pipes it over
SSH to `python3 -m app_installer import-replication-secrets` on standby. The
project-level `ansible.cfg` enables pipelining, so Ansible does not need normal
module transfer files or a helper image.

The import checks every secret before it writes any. It refuses a transfer that
is not exactly the complete group. If an existing standby secret has a different
value, it names that secret, creates nothing and the playbook stops. Otherwise
it creates only the missing secrets. No plaintext secret file or command-line
password is created, and error messages name secrets without showing values. After provisioning, each host has its own
local Podman secret objects, so standby does not need primary during failover.

This is bootstrap transfer, not a centralized secret backup. The demo assumes
at least one database node survives with the required Podman secrets. Simultaneous
loss of both nodes is outside scope; see [Secrets](../../docs/SECRETS.md).
