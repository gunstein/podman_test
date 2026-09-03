# Operator-side DR acceptance automation

`scripts/lab_dr_acceptance.py` is the controller for the two disposable
Proxmox Oracle Linux lab VMs. It complements the production-facing
`todo_dr_run.py`: the lab controller owns snapshot reset and acceptance
evidence, while the existing DR runner retains promotion and rebuild safety.

The first implemented profile is deliberately small:

```text
reset_hosts       destructive snapshot rollback
clean_preflight   read-only Oracle Linux and empty Podman checks
```

It verifies both VM IDs against their exact Proxmox names and requires both
configured snapshots before changing power or disk state. A started reset is
never automatically retried. QEMU Guest Agent and SSH must both answer after
the rollback.

Copy the example outside Git-tracked configuration:

```bash
cp lab-dr.example.toml lab-dr.local.toml
```

Set the actual Proxmox SSH target, key paths and exact snapshot names. The
configuration contains topology but no credentials. The local file is ignored
by Git.

Safe inspection does not touch the VMs:

```bash
python3 scripts/lab_dr_acceptance.py \
  --config lab-dr.local.toml \
  validate --local-only

python3 scripts/lab_dr_acceptance.py \
  --config lab-dr.local.toml \
  plan
```

The default state is
`~/.local/state/todo/lab-dr-acceptance.json`. It is mode `0600` and binds a
run to the exact configuration fingerprint and profile.

Run the destructive reset only after checking the plan:

```bash
python3 scripts/lab_dr_acceptance.py \
  --config lab-dr.local.toml \
  run --confirm-reset 107:108
```

The confirmation value is derived from the configured primary and standby VM
IDs. The controller stops both VMs, rolls each back to its configured clean
snapshot, starts both, waits for QGA and SSH, then requires:

- the exact guest hostnames and configured IP addresses;
- distinct systemd machine IDs;
- SELinux `Enforcing`;
- active sshd, firewalld, fapolicyd and QEMU Guest Agent;
- rootless Podman and user lingering; and
- no containers, volumes or Podman secrets.

Show progress or emit a machine-readable report:

```bash
python3 scripts/lab_dr_acceptance.py \
  --config lab-dr.local.toml status

python3 scripts/lab_dr_acceptance.py \
  --config lab-dr.local.toml report --json
```

## Proxmox access

The controller requires Python 3.11 or newer and has no third-party Python
dependencies.

The controller currently uses `qm` over BatchMode SSH because it is available
on the installed Proxmox node and keeps the command surface visible. Do not
give it unrestricted root SSH in normal use. Create a dedicated account or
forced-command key restricted to read, stop, rollback, start and guest-agent
operations for VM 107 and 108.

The controller uses strict host-key checking for Proxmox. Guest SSH accepts a
previously unseen key once and rejects a changed known key. The clean snapshots
must contain the configured operator public key, or it must be injected through
an independently controlled mechanism before running the profile.

## Planned profiles

The next stages will reuse the existing offline bundle, Ansible roles and
`todo_dr_run.py`:

```text
full-clean:
  reset/check -> install -> replication -> marker -> fence -> promote
  -> application -> quarantine/rebuild -> sequential reboot -> verify

full-resilience:
  full-clean -> backup/PITR -> PostgreSQL Kube migration
  -> grouped application migration -> rollback/verify
```

Before rebuild is automated, reintroduction of the old primary must use a
tested hypervisor quarantine plus QGA to stop its Todo user services before its
network is reopened. A firewall rule that only blocks PostgreSQL is not a
sufficient boundary.
