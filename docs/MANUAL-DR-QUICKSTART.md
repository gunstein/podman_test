# Short manual-reset DR routine

This is the operator index for the next clean-state drill, not permission to
reset the currently working pair. The September 5 repaired run passed the DR
chain; a fresh run from one clean revision remains required.

## One topology file, printed commands

On the ThinkPad, use the existing ignored `lab-dr.local.toml`, or copy
`lab-dr.example.toml` to that name if no local file exists. Edit
`nodes.primary` and `nodes.standby` (VM ID, hostname, address and Proxmox name),
and `guest_ssh.user`. Names describe the INITIAL roles and never swap in this
file after promotion. The existing format also contains snapshot and Proxmox
SSH settings for the separate reset controller; this command generator does
not use them to connect, reset or control Proxmox.

```bash
python3 scripts/manual_dr_commands.py --config lab-dr.local.toml status
```

Replace `status` with a phase in the table below. The generator prints a
quoted command to review and paste into the ThinkPad terminal. It never runs
it. Never pipe its output to a shell. It requires Python 3.11+ or the existing
`tomli` fallback on older Python. SSH host checking remains enabled.

This does NOT configure guest addresses, Ansible inventories, DHCP, firewall
rules or `/etc/hosts`. Keep those aligned using the
[address map](LAB-ACCEPTANCE.md#where-to-change-vm-addresses). A different IP in
TOML alone does not reconfigure an existing replicated pair.

## Phase checklist

| Phase | Operator boundary / evidence |
|---|---|
| Clean preparation | Manually reset both VMs only when agreed. Build BOTH packages from one clean revision, verify checksums and VERSION. Follow LAB-ACCEPTANCE sections 1–5. |
| `prepare-quarantine` | Explicit Guest Agent RPC and SELinux security opt-ins. Rehearse the [quarantine procedure](PROXMOX-QUARANTINE.md) before fencing. |
| Fence in Proxmox node Shell | Verify correct VM, no HA auto-restart, `onboot=0`, stopped VM and all links disconnected. No hypervisor SSH or guest-console typing. |
| `promote` | Only after verified fencing and zero local apply lag. Old primary must never return unrestricted. |
| `application` | Open only client HTTPS on promoted host. Then switch `todo.test`, trust exported CA and verify authenticated writes. |
| `application-repeat` | Require changed=0; runner intentionally skips already completed application phase, so use the playbook for this repeat. |
| `backup` | Configure archive, perform [full isolated PITR](../ansible/BACKUP-PITR.md), repeat this phase for changed=0 and reboot current primary. Backup is still on-VM, not off-host protection. |
| Quarantine in Proxmox | Boot old primary disconnected, execute labelled helper directly, require completed exitcode=0 and STOPPED, inspect active rules, only then reconnect restricted SSH. |
| `rebuild-preflight` | Read-only. Verify roles, stopped old services, credentials and absent new slot. Prepare guest firewalls and the single outbound replication exception. |
| `rebuild` | DELETES old primary database. Authenticated IDENTIFY_SYSTEM must pass BEFORE deletion. Never blindly retry a partial rebuild. |
| `cluster-status` | Fresh read-only check, even when runner verification is already marked completed. Require streaming, zero lag, active usable slot, read-only standby, healthy archive. |
| Final reboots | Reboot rebuilt standby first, verify; then current primary, verify. Never both together. Check authenticated marker on standby, TLS, issuer, backup and no failed units. |

Keep Proxmox quarantine on through rebuild and verification. After rebuild,
the old stop helper is not a normal standby-management command: it expects all
three original Todo units, while rebuilt standby only has PostgreSQL.

## Chromium trust, once per new lab CA

System trust and Chromium trust are separate on this Linux test client.
Retrieve only the public CA via verified SSH from the promoted host and compare
its hash before import. Never import private keys. Install `libnss3-tools` on
the ThinkPad if needed. Chromium uses the existing `~/.pki/nssdb`, or for newer
versions the default `~/.local/share/pki/nssdb` when the old database is absent.
Inspect the actual database first; do not overwrite another certificate.

The tested import used a unique fingerprint-derived nickname and TLS-CA trust
only (`C,,`). Substitute the reviewed database, nickname and public CA path:

```bash
certutil -L -d sql:/path/to/nssdb
certutil -A -d sql:/path/to/nssdb -n todo-lab-ca-FINGERPRINT -t 'C,,' -i /path/to/public-root.crt
E2E_BASE_URL=https://todo.test:8443 E2E_IGNORE_HTTPS_ERRORS=false backend/.venv/bin/python -m pytest e2e/test_todo_flow.py --browser chromium -q
```

The authenticated test requires the existing secure test-user provisioning
and E2E credentials in memory; a skipped test is not PASS. Do not record those
credentials in this guide or shell history. Remove only the exact lab nickname
when trust is retired using `certutil -D -d sql:/path/to/nssdb -n NAME`.
See [Chromium's certificate documentation](https://chromium.googlesource.com/chromium/src/+/master/docs/linux/cert_management.md).

The full pass criteria and detailed commands remain in
[LAB-ACCEPTANCE.md](LAB-ACCEPTANCE.md). Record partial failures honestly: a
repaired run is evidence for the repaired phases, not a clean-revision pass.
