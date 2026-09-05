# Recover the old primary without its VM console

Status (2026-09-05): lab quarantine rehearsal passed on Proxmox 8.4.16.
Disconnected shutdown/start, Guest Agent STOPPED, restricted IPv4 SSH and
new-connection blocking were tested. IPv6 link-local SSH timed out both ways
under quarantine and connected both ways with quarantine disabled. Installed
IPv4/IPv6 iptables chains were inspected. Promotion, backup/PITR, old-primary
rebuild and sequential final reboots subsequently passed. The repaired drill
does not replace a new clean-revision acceptance run. See the project journal
and [MANUAL-DR-QUICKSTART.md](MANUAL-DR-QUICKSTART.md) for the phase checklist.
Do not enable a datacenter/node firewall based only on this document: that
can affect management access and unrelated VMs.

The guest console is not used. The operator uses Proxmox controls and one
short command in the **Proxmox node's Shell**, not VM 107's Console.
The assistant uses SSH after the isolated guest's Todo services are stopped.

## One-time preparation while both machines are healthy

1. Record Proxmox version, active firewall backend, datacenter/VM/NIC enable
   flags, all VM 107 network devices and any passthrough networking. Review
   existing rules and management access before changing anything.
2. Verify QEMU Guest Agent is enabled for VM 107 and works from the host.
3. Install the stop helper on the initial primary using the operations package:

   ```bash
   ansible-playbook --ask-become-pass --inventory ansible/inventory-initial.ini ansible/install-quarantine-tool.yml
   ```

   This installs one root-owned file with exact fapolicyd trust.
   It does not stop services.

   If Guest Agent answers ping but rejects `guest-exec`, the operator may
   explicitly authorize execution support on the initial primary:

   ```bash
   ansible-playbook --ask-become-pass --inventory ansible/inventory-initial.ini ansible/install-quarantine-tool.yml -e todo_quarantine_enable_guest_exec=true
   ```

   This grants Proxmox administrators arbitrary root command execution in
   this VM, not just permission to run the helper. The opt-in preserves the
   existing explicit `/etc/sysconfig/qemu-ga` allow list, adds only
   `guest-exec` and `guest-exec-status`, backs up the configuration and
   restarts only Guest Agent if changed. Unknown policy formats fail closed.
   Todo services and firewall settings are not changed. The lab's Proxmox
   8.4.16 installation initially had datacenter Firewall disabled. Following
   review it is enabled, with node filtering explicitly disabled. VM 107
   filtering remained enabled after the final rebuild, including the restricted
   outbound replication exception. Do not assume clean snapshots reset these
   hypervisor firewall settings; review them before the next clean drill.

   On the enforcing lab guest, direct bash execution was denied by
   `virt_qemu_ga_t` when accessing `hostname_exec_t`. With separate operator
   approval, add `-e todo_quarantine_enable_selinux_entrypoint=true` to the
   installer above. This persistently enables `virt_qemu_ga_run_unconfined`
   and labels only `/opt/todo/bin/todo-quarantine.sh` as
   `virt_qemu_ga_unconfined_exec_t`, root-owned mode 0755. The boolean also
   permits transitions for other appropriately labelled Guest Agent hooks;
   it is not a helper-only SELinux permission. SELinux stays Enforcing and
   fapolicyd exact-file trust remains required. No automatic fsfreeze hook
   is installed. No service stop is performed by installation.

   Execute this labelled file directly: passing it to `/usr/bin/bash`
   does not trigger the required entrypoint transition. Verify the check
   through the real Guest Agent before relying on this procedure.
   To revoke the newly granted transition later, use
   `sudo setsebool -P virt_qemu_ga_run_unconfined off` inside the VM;
   this also affects other hooks using that boolean.

   From the Proxmox **node Shell**, test only its read-only mode:

   ```bash
   qm guest exec 107 -- /opt/todo/bin/todo-quarantine.sh check todo-primary gunstein
   ```

   Require a completed Guest Agent response with `exitcode: 0` and `READY`.
   A returned PID alone is not completion; use `qm guest exec-status 107 PID`.
4. Prepare a VM-only quarantine profile, initially inactive. Its requirements:

   | Direction | Permit | Phase |
   |---|---|---|
   | IN | TCP 22 from ThinkPad's observed source `192.168.0.100/32` | Management, after services stopped |
   | IN | TCP 22 from promoted host `192.168.0.108/32` | Management, after services stopped |
   | OUT | TCP 5432 to promoted host `192.168.0.108/32` | Only when preparing guarded rebuild |
   | IN/OUT | Deny other application traffic, for both IPv4 and IPv6 | Throughout quarantine |

   Established SSH replies must work. Review DHCP/IPv6 control exceptions and
   every NIC. Blocking only inbound PostgreSQL does not isolate the old host.
   Exact GUI clicks depend on the verified Proxmox version and existing rules.
5. Rehearse the isolation and Guest Agent stop procedure **before promotion**,
   with an agreed brief lab outage. Verify fresh inbound and outbound test
   connections, plus a permitted SSH connection after STOPPED. A blocked port
   with no listening service is not proof of a working firewall. Verify the
   effective hypervisor rules as well. Keep the network disconnected if any
   check fails. Restoration before promotion may restart the original primary;
   restoration after promotion may only rebuild it as a standby.

## The short recovery sequence, after promotion and backup/PITR

| Operator action | Required result before proceeding |
|---|---|
| With VM 107 still Stopped, disconnect **all** its virtual network links | Hypervisor configuration shows all links disconnected |
| Activate the previously tested VM quarantine profile | No client/database access to the old primary; replication exception still disabled |
| Start VM 107 with links disconnected | Guest Agent answers; guest network remains isolated |
| Run the single stop command below in the node Shell | Completed response: `exitcode: 0` and `STOPPED` |
| Reconnect the links with quarantine still enforced | Assistant verifies restricted SSH, stopped Todo services and no containers |

```bash
qm guest exec 107 -- /opt/todo/bin/todo-quarantine.sh stop todo-primary gunstein
```

If the command fails, times out, or returns only a PID, leave links disconnected
and inspect its execution status. Do not reconnect based on a `qm` exit status
alone. The helper validates hostname, user and service states. It cannot prove
hypervisor isolation and is not a substitute for fencing.

An isolated DHCP boot can leave the configured publish address unavailable:
`rootlessport ... bind: cannot assign requested address`. The PostgreSQL unit
may then remain `failed` even after a successful stop. The helper accepts
`inactive` or `failed` only after stopping all three units, requiring zero
MainPID and ControlPID for each and no running user containers. It warns about
failed units without clearing their failure state. Read the journal over
quarantined SSH using `journalctl -b _SYSTEMD_USER_UNIT=todo-postgres.service`;
do not assume `journalctl --user` can see the same journal on this host.

The assistant then performs the existing rebuild preflight. Enable only the
documented outbound replication exception, retain the promoted-host firewall
restriction, and prove authenticated replication before destructive reseeding.
Fencing confirmation, backup/PITR and one-shot rebuild checks remain mandatory.
Do not reboot the old host again before rebuild completes: stopping a service
does not disable it at boot. Quarantine remains enforced until recovery and
streaming have been verified. The original machine returns as database standby.

## Address changes and sources

VM 107, `todo-primary`, `gunstein` and both allowed source addresses are lab
values; record replacements before use. See the address table in
[LAB-ACCEPTANCE.md](LAB-ACCEPTANCE.md#where-to-change-vm-addresses).
Proxmox firewall enable flags and rule semantics are described in the
[official firewall documentation](https://pve.proxmox.com/pve-docs/chapter-pve-firewall.html).
See [qm](https://pve.proxmox.com/pve-docs/qm.1.html) for Guest Agent execution.
