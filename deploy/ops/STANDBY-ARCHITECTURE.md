# Initial primary and standby preparation

The primary host is the app-ops controller during normal operation. The
standby must nevertheless contain everything needed for local promotion;
failover must not depend on the primary still being available.

Write the initial inventory on primary, in the extracted operations package
(see [the inventory format](README.md#inventory)):

```yaml
user: gunstein
hosts:
  todo-primary: {role: primary, address: <primary-address>, local: true}
  todo-standby: {role: standby, address: <standby-address>}
```

The primary entry is marked `local: true`, so app-ops runs its commands
without SSH. Test SSH with host-key checking before running app-ops. After
restoring a VM snapshot, verify the
standby's current host-key fingerprint through an independently verified
connection before accepting a new key on primary; do not use an unverified `ssh-keyscan` result as trust evidence.

The primary also needs a non-interactive user key for app-ops. Re-create and
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
python3 -m app_ops --inventory initial.yaml sync-standby-secrets
```

`bootstrap-standby` runs the same step itself, so this is needed on its own
only to check or refresh the copy. app-ops is only the transport here. On
primary, `python3 -m app_installer export-replication-secrets` reads every
credential of the complete replication group with `podman secret inspect
--showsecret` and prints them as one opaque base64 value. app-ops keeps that
value in memory, never logs it, and pipes it over SSH to `python3 -m
app_installer import-replication-secrets` on standby. No file and no helper
image is involved.

The import checks every secret before it writes any. It refuses a transfer that
is not exactly the complete group. If an existing standby secret has a different
value, it names that secret, creates nothing and the command stops. Otherwise
it creates only the missing secrets. No plaintext secret file or command-line
password is created, and error messages name secrets without showing values. After provisioning, each host has its own
local Podman secret objects, so standby does not need primary during failover.

This is bootstrap transfer, not a centralized secret backup. The demo assumes
at least one database node survives with the required Podman secrets. Simultaneous
loss of both nodes is outside scope; see [Secrets](../../docs/SECRETS.md).
