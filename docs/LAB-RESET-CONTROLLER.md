# Optional lab reset controller

For the full acceptance workflow, start with
[MANUAL-DR-QUICKSTART.md](MANUAL-DR-QUICKSTART.md).
This document covers only `scripts/lab_dr_acceptance.py`, not production DR.
The controller is optional; the tested manual-reset workflow does not need
SSH access to Proxmox.

## Implemented scope

The only implemented profile is `reset-check`:

```text
reset_hosts       destructive snapshot rollback of both disposable VMs
clean_preflight   read-only identity, security and Todo-state checks
```

There is no full-clean automation profile. Mechanical work and assertions are
automated by Ansible and the operational tools; fencing, security opt-ins,
promotion and destructive reseeding remain explicit operator boundaries.
The tools have separate jobs:

| Tool | Scope |
|---|---|
| lab_dr_acceptance.py | Optional lab reset and clean checks |
| manual_dr_commands.py | Print reviewed guest commands; never execute them |
| todo_dr_run.py | Guarded promotion, application and rebuild stages |
| Ansible | Provision and verify runtime and recovery state |
| MANUAL-DR-QUICKSTART.md | Operator phase order, evidence and handoff |

## Configuration and safe inspection

Use an ignored local topology file. If `lab-dr.local.toml` already exists,
edit it rather than overwriting it with the example. Configure actual VM IDs,
exact Proxmox names and snapshot names, guest addresses and SSH identities.
Snapshots must already contain the operator public key, or an independently
controlled mechanism must establish guest access after rollback.

Python 3.11+ needs no extra library. Older supported Python uses the existing
pinned `tomli` dependency. From the client/build host:

```bash
python3 scripts/lab_dr_acceptance.py --config lab-dr.local.toml validate --local-only
python3 scripts/lab_dr_acceptance.py --config lab-dr.local.toml plan
```

These commands do not reset the VMs. The default private state file is
`~/.local/state/todo/lab-dr-acceptance.json`, mode 0600, bound to the config
fingerprint and profile. Inspect it through the CLI; never clear a failed
destructive stage merely to allow retry.

## Destructive reset: optional, explicit approval required

The controller uses BatchMode SSH to Proxmox and `qm`. It verifies VM IDs
against names and requires the configured snapshots before mutation. Configure
and review least-privilege hypervisor access separately; this tool does not
provision or validate an SSH authorization policy for you. Do not give it
unrestricted root SSH simply to complete a test.

ONLY after approval to discard both VMs' current test data, inspect the plan
and use the confirmation matching your configured IDs (107:108 is an example):

```bash
python3 scripts/lab_dr_acceptance.py --config lab-dr.local.toml run --confirm-reset 107:108
```

A started reset is not automatically retried. The controller stops both VMs,
rolls back the snapshots, starts the VMs and waits for Guest Agent and SSH.
It uses strict host checking for Proxmox; guest SSH accepts an unseen key once
and rejects changed known keys. This is different from independently verifying
every new key; use the manual procedure when that distinction matters.

Snapshot rollback does not prove external Proxmox firewall state has reset.
Review hypervisor firewall/quarantine separately before using the resulting lab.

## What clean-preflight actually checks

It requires expected hostnames/IPs, distinct machine IDs, enforcing SELinux,
active sshd/firewalld/fapolicyd/Guest Agent, rootless Podman and user lingering.
It rejects Todo-named containers, volumes and secrets, `todo-network`,
matching Todo Quadlets, `~/.config/todo` and installed DR/backup tool paths.
Unrelated Podman resources are not rejected by these name-scoped checks.
This is a baseline check, not proof that every byte matches the snapshot.

```bash
python3 scripts/lab_dr_acceptance.py --config lab-dr.local.toml status
python3 scripts/lab_dr_acceptance.py --config lab-dr.local.toml report --json
```

A successful reset-check is NOT acceptance of installation, failover or backup.
Continue with the operator checklist, fresh evidence and one unchanged clean
revision. Do not infer permission for promotion or rebuild from reset approval.
