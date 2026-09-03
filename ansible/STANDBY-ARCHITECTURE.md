# Initial primary and standby preparation

The primary host is the Ansible controller during normal operation. The standby
must nevertheless contain everything needed for local promotion; failover must
not depend on the primary still being available.

Copy and edit the example inventory on primary:

```bash
cp ansible/inventory-initial.example.ini ansible/inventory-initial.ini
```

Replace the example standby address and adjust `ansible_user` if necessary. The
primary entry deliberately uses a local connection. Test SSH with host-key
checking before running Ansible. After restoring a VM snapshot, verify the
standby's current host-key fingerprint from its console before accepting a new
key on primary; do not use an unverified `ssh-keyscan` result as trust evidence.

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

The final command must return the standby hostname without asking for a
password. Keep the private key only on primary and never add it to an archive or
the repository.

Ensure the pinned PostgreSQL 17.11 image is available on both hosts. Then copy the
existing credentials from primary to standby:

```bash
ansible-playbook \
  --inventory ansible/inventory-initial.ini \
  ansible/sync-standby-secrets.yml
```

The playbook reads each existing primary secret with `podman secret inspect
--showsecret`. Ansible suppresses the values with `no_log`, keeps them in memory
and sends them over SSH directly to `podman secret create` on standby. The
project-level `ansible.cfg` enables pipelining, so Ansible does not need normal
module transfer files or a helper image.

Only missing secrets are created. If a secret already exists with a different
value, the playbook stops instead of overwriting it. No plaintext secret file or
command-line password is created. After provisioning, each host has its own
local Podman secret objects, so standby does not need primary during failover.

This is bootstrap transfer, not a centralized secret backup. The demo assumes
at least one database node survives with the required Podman secrets. Simultaneous
loss of both nodes is outside scope; see [Secrets](../docs/SECRETS.md).
