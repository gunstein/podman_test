# Short manual-reset DR routine

**Start here — for both humans and agents.** Use this checklist as the single
entry point. Follow linked detailed commands only for the current phase;
do not reconstruct the procedure from a chat transcript.

This is the operator index for the next clean-state drill, not permission to
reset the currently working pair. The September 5 repaired run passed the DR
chain; a fresh run from one clean revision remains required.

Deployment bundles intentionally exclude legacy learning guides and runtime
transition playbooks/roles; those remain in the source repository until the
retirement gate passes. The optional reset controller is documented in the
source-only docs/LAB-RESET-CONTROLLER.md, separate from this manual workflow.

## Before touching the VMs

1. Choose the entry path. **NEW:** use the current architecture, this checklist,
   LAB-ACCEPTANCE.md and the selected revision; start from the documented clean
   baseline after reset approval. Never infer current state from an earlier run.
   **CONTINUATION:** read the private run record and obtain fresh role, fencing
   and runner status before acting. Never reset or repeat promotion/rebuild just
   to resume a chat. PROJECT.md is optional history, not an input requirement.
2. Agree whether this is a NEW destructive clean run or continuation. Reset,
   promotion and replacement of old database data need explicit operator
   agreement. A general request to continue is not permission to erase a
   working pair. Keep a verified backup before rebuild.
3. Fill in the topology/address map below and verify real VM IDs, NIC settings,
   snapshot names and client source address. Snapshot rollback does NOT prove
   Proxmox firewall rules were reset; inspect them separately. Never guess a
   firewall rule by its position without reading its full contents first.
4. Use one clean revision for both bundles. Store run evidence outside the
   source checkout during a clean run. Changes during the run make it REPAIRED,
   even when all final functional checks pass.

### Who runs what, and where

| Location | Responsibility |
|---|---|
| Client/build terminal (ThinkPad in this lab) | Build, transfer, run printed guest SSH commands, configure client DNS/CA and execute browser tests. An agent with access can do scoped checks here. |
| Proxmox **node Shell** | Operator pastes reviewed `qm`/`pvesh` commands. No SSH to the hypervisor and no typing in the guest console are required by this procedure. |
| Guest, reached through SSH | Ansible and rootless Podman run as the service user. Use `ssh -t` and `--ask-become-pass` for privileged installation, including rebuild. Never send sudo passwords to an agent. |

Paste only the command block, not prompts such as `root@proxmox:~#`.
After each operator action, inspect its result before giving the next mutation.
Batch independent read-only checks; do not batch across a fencing or deletion
gate. Agents should run available checks themselves instead of asking the
operator to copy logs repeatedly. Report the next location explicitly.

### Run record / handoff template

Copy this into a private run log (no passwords, tokens or secret payloads):

```text
Run ID / operator / date:
Mode: NEW clean run | CONTINUATION
Git revision / both VERSION values / archive checksums:
Topology: initial primary hostname/IP/VMID/NIC; initial standby equivalents;
          client source IP; Proxmox node; actual clean snapshot names:
Current roles and fencing: which DB is writable, VM power/link/firewall state:
Last completed phase / exact command / recap and evidence:
Next phase / where to run it / approval still required:
Markers: original Todo ID/title; final authenticated Todo ID/title:
Backup name / restore-point name / isolated comparison / cleanup:
Boot IDs before/after / TLS CA fingerprint / browser tests (no skips):
Deviations, repairs and runner state (keep original failure evidence):
Verdict: IN PROGRESS | BLOCKED | REPAIRED FUNCTIONAL PASS | CLEAN PASS
```

## One topology file, printed commands

On the client/build host, use the existing ignored `lab-dr.local.toml`, or copy
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
quoted command to review and paste into the client/build terminal. It never runs
it. Never pipe its output to a shell. It requires Python 3.11+ or the existing
`tomli` fallback on older Python. SSH host checking remains enabled.

This does NOT configure guest addresses, Ansible inventories, DHCP, firewall
rules or `/etc/hosts`. Keep those aligned using the
[address map](LAB-ACCEPTANCE.md#where-to-change-vm-addresses). A different IP in
TOML alone does not reconfigure an existing replicated pair.

## Phase checklist

This table is an index. The numbered phase cards in [LAB-ACCEPTANCE.md](LAB-ACCEPTANCE.md)
own prerequisites, PASS/STOP criteria and evidence. Do not execute the index as
a second sequence. At handoff record the numbered phase and next unexecuted action.

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

## Stop conditions and recovery from known failures

| Observation | Safe next action |
|---|---|
| `qm guest exec` returns only a PID | Keep links disconnected; poll `qm guest exec-status VMID PID`. Require `exited=1`, `exitcode=0` and `STOPPED`; the outer `qm` exit code is insufficient. |
| `pve-firewall status` says pending changes | Wait and inspect again. Verify actual IPv4 AND IPv6 rules before reconnecting under quarantine. |
| PostgreSQL failed on disconnected DHCP boot | Inspect the journal for `bind: cannot assign requested address`. The updated helper accepts inactive/failed only after stop, zero service PIDs and no running containers; it preserves the failure warning. Do not start the old database after promotion. |
| Helper reports hostname missing after an update | Inspect `ls -lZ` through Guest Agent. Required label: `virt_qemu_ga_unconfined_exec_t`. The updated installer restores existing persistent policy after replacement. Do not disable SELinux or broadly enable Guest Agent commands. |
| Direct Guest Agent diagnostic is denied | Do not keep guessing privileged commands. Use reviewed restricted SSH only after stop/process/container and active firewall evidence is available. Read `journalctl -b _SYSTEMD_USER_UNIT=todo-postgres.service`; `--user` may see no journal here. |
| fapolicyd trust task retries then succeeds | Normal asynchronous refresh; judge final recap. If exhausted, inspect exact path/size/hash trust, never trust whole directories or disable fapolicyd. |
| `/ready` works but login is still 503 after boot | Wait for Keycloak/discovery and browser tests too. `/ready` alone is not whole-application acceptance. |
| Replication is absent immediately after reconnect | Check receiver logs and bounded reconnect progress. A prior TCP attempt can still be timing out. Require streaming and zero lag before the next gate; do not restart or reseed blindly. |
| Rebuild fails at any point | STOP. Record the failed task and runner state. Inspect both roles, slot, volumes and streaming before deciding what remains. Never repeat the destructive command. |

### Specific recovery: rebuild succeeded, only DR-tool installation failed

This applies ONLY to the observed missing-become-password failure after base
backup and recovery startup. First independently verify `.108` writable,
`.102` read-only, `todo_rebuilt_standby` streaming, and quarantine intact.
Then the operator may finish the non-destructive tail on the promoted host:

```bash
cd "$HOME/todo-operations"
ansible-playbook --ask-become-pass \
  --inventory ansible/inventory-recovery.ini ansible/rebuild-standby.yml \
  --start-at-task "Create the Todo DR configuration directory"
ansible-playbook --inventory ansible/inventory-recovery.ini ansible/cluster-status.yml
```

This is an exceptional repair, NOT the normal rebuild command. If the task name
or failure boundary differs, stop and inspect the current playbook. The runner
still records the original rebuild failure; there is currently no supported
reconciliation command. Do not edit it to completed or rerun rebuild to turn it
green. Record manual completion and direct verification in the run log.

## What counts as complete

A CLEAN PASS needs every phase above from the same clean revision, no skipped
authenticated tests, isolated PITR comparison, both sequential final reboots,
fresh cluster status, and a new authenticated marker read on rebuilt standby.
Preserve evidence of repairs: a REPAIRED FUNCTIONAL PASS is useful, but does not
satisfy the unchanged-revision gate. Do not reset the pair for a new clean run
without new approval. The on-VM backup is not protection against VM/host loss.

## Chromium trust, once per new lab CA

System trust and Chromium trust are separate on this Linux test client.
Retrieve only the public CA via verified SSH from the promoted host and compare
its hash before import. Never import private keys. Install `libnss3-tools` on
the client/build host if needed. Chromium uses the existing `~/.pki/nssdb`, or for newer
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
