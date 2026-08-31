# M13 primary and standby preparation

The primary host is the Ansible controller during normal operation. The standby
must nevertheless contain everything needed for local promotion; failover must
not depend on the primary still being available.

Copy and edit the example inventory on primary:

```bash
cp ansible/inventory-initial.example.ini ansible/inventory-initial.ini
```

Replace the example standby address and adjust `ansible_user` if necessary. The
primary entry deliberately uses a local connection. Test SSH with host-key
checking before running Ansible:

```bash
ssh gunstein@<standby-address> hostname
```

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

This is bootstrap transfer, not secret backup. Recovery after loss of both
hosts requires a separately protected credential backup, planned for M15.
