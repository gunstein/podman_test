# Recover the old primary without its VM console

Status: prepared procedure, not yet accepted on this Proxmox installation.
The helper has local tests. Proxmox version, firewall backend, effective
rules and Guest Agent execution must be checked before fencing primary.
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

   This installs one root-owned file with exact fapolicyd trust. It does not
   stop services. From the Proxmox **node Shell**, test only its read-only mode:

   ```bash
   qm guest exec 107 -- /usr/bin/bash /opt/todo/bin/todo-quarantine.sh check todo-primary gunstein
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
| Reconnect the links with quarantine still enforced | Assistant verifies restricted SSH, inactive Todo services and no containers |

```bash
qm guest exec 107 -- /usr/bin/bash /opt/todo/bin/todo-quarantine.sh stop todo-primary gunstein
```

If the command fails, times out, or returns only a PID, leave links disconnected
and inspect its execution status. Do not reconnect based on a `qm` exit status
alone. The helper validates hostname, user and service states. It cannot prove
hypervisor isolation and is not a substitute for fencing.

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
