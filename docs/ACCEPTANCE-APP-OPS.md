# Acceptance with app-ops

This guide runs the two-VM acceptance in [ACCEPTANCE.md](ACCEPTANCE.md) with
`app-ops` in place of the Ansible playbooks. `app-ops` is the plain-SSH
operations tool in `deploy/ops` (see [its README](../deploy/ops/README.md)).
The goal is to show that it can replace Ansible for DR and multi-host
operations on real hosts: same hosts, same phases, same safety gates.

This is not a second acceptance sequence. Follow ACCEPTANCE.md phase by phase,
with its approvals, evidence, PASS and STOP conditions. Where a phase runs
`ansible-playbook`, run the `app-ops` command from this guide instead.
Everything else stays as written, including every direct `app_dr.py`,
`app_backup.py`, `podman`, `firewall-cmd` and Proxmox step. The extra checks
below prove the things that are new with `app-ops`: non-interactive sudo, exact
host identity, clear refusals and repeat runs that change nothing.

A pass here is evidence for retiring the Ansible playbooks. Retiring them is a
separate decision.

An autonomous agent run uses [ACCEPTANCE-AGENT.md](ACCEPTANCE-AGENT.md) with
`Operations tool: app-ops`. Its section C9.13 says how the agent handles sudo,
trust and inventories.

## What changes

| Phase | Ansible playbook | app-ops command | Runs on (controller) |
|---|---|---|---|
| 4 | `preflight-standby.yml` | `preflight-standby` | Initial primary |
| 4 | `bootstrap-standby.yml` | `bootstrap-standby` | Initial primary |
| 4 | `replication-status.yml` | `replication-status` | Initial primary |
| 5 | `install-dr-tool.yml` | `install-dr-tool` | Initial primary |
| 5 | `install-quarantine-tool.yml` | `install-quarantine-tool` | Initial primary |
| 7 | `deploy-promoted-application.yml` | `deploy-promoted-application` | Promoted host |
| 8 | `configure-backup.yml` | `configure-backup` | Promoted host |
| 9 | `preflight-standby-rebuild.yml` | `preflight-standby-rebuild` | Promoted host |
| 9 | `rebuild-standby.yml` | `rebuild-standby` | Promoted host |
| 9-11 | `cluster-status.yml` | `cluster-status` | Promoted host |

The controller is the VM you run `app-ops` on. It is always one of the two
VMs, marked `local: true` in the inventory, and reaches the other VM over
SSH. Ansible is not used anywhere in this run. It may stay installed, but no
phase calls it.

Add one line to the ACCEPTANCE.md run record:

```text
Operations tool: app-ops (deploy/ops), no Ansible
```

## Before phase 4: SSH, sudo and trust

- **Where:** Both VMs through SSH from the client/build host.
- **Preconditions:** Phases 1-3 passed. Explicit operator approval for passwordless sudo during the run.
- **PASS:** `sudo -n true` works on both VMs, and app-ops refuses cleanly before the approval takes effect.
- **Evidence:** Negative sudo output, sudoers file review, and `sudo -n true` on both VMs.
- **STOP if:** A password prompt appears anywhere, or the refusal changed anything.

### Trust app-ops on each controller

With fapolicyd active, app-ops is project Python that must be trusted before
it runs. On the initial primary now, and on the initial standby before phase 7
(it becomes the controller after promotion):

```bash
cd "$HOME/todo-operations"
sha256sum -c SHA256SUMS
sudo sh deploy/scripts/trust-files.sh trust todo \
  "$PWD"/deploy/ops/app_ops/*.py "$PWD"/deploy/installer/app_installer/*.py
```

This is the one step where sudo still asks for a password, typed by the
operator. Require `changed` the first time and `unchanged` when repeated. The trust
applies to these exact files. Repeat it after replacing the package.

### Inventories

Two small YAML files in `$HOME/todo-operations`. Host names must match
`hostname` on each VM exactly. Addresses must match `ip -4 address`. app-ops
checks both and refuses a mismatch. Use the lab addresses from ACCEPTANCE.md.

`initial.yaml` on the initial primary:

```yaml
user: gunstein
hosts:
  todo-primary: {role: primary, address: 192.168.0.102, local: true}
  todo-standby: {role: standby, address: 192.168.0.108}
```

`recovery.yaml` on the promoted host, before phase 7. The machines keep their
names, but their roles reverse:

```yaml
user: gunstein
hosts:
  todo-standby: {role: current_primary, address: 192.168.0.108, local: true}
  todo-primary: {role: rebuild_standby, address: 192.168.0.102}
```

Home and bundle default to `/home/<user>` and `<home>/todo-offline-m12`, as in
phase 2. Every command below starts from the package directory with:

```bash
cd "$HOME/todo-operations"
export PYTHONPATH="$PWD/deploy/ops" PYTHONDONTWRITEBYTECODE=1
```

Each command prints one JSON line on success, and one `app-ops:` error line on
stderr with exit 1 on failure. Record both in the evidence.

### Passwordless sudo, and why it needs approval

`app-ops` runs privileged steps as `sudo -n`. It never reads, sends or stores
a password. The Ansible run used `--ask-become-pass` instead. Both VMs need
passwordless sudo for the service user: the controller trusts and stages files
locally, and the other VM installs them.

This gives the service user root without a password. Whoever can run commands
as that user, an agent included, can then do anything as root on that VM.
Approving it is the operator's access decision. For the run, grant it for the
run only, as a separate file that is easy to see and remove.

First prove the refusal. On the initial primary, before any sudoers change,
with the trust and `initial.yaml` from above in place:

```bash
cd "$HOME/todo-operations"
sudo -k
export PYTHONPATH="$PWD/deploy/ops" PYTHONDONTWRITEBYTECODE=1
python3 -m app_ops --inventory initial.yaml preflight-standby; echo "exit=$?"
```

Require exit 1 and a message naming passwordless sudo (NOPASSWD) for
the user. Require no password prompt. Nothing changed: the first privileged
step was refused.

After approval, on each VM, as the operator who knows the sudo password:

```bash
sudo visudo -f /etc/sudoers.d/90-app-ops-acceptance
```

Enter exactly one line, with the real service user:

```text
gunstein ALL=(root) NOPASSWD: ALL
```

Then check:

```bash
sudo cat /etc/sudoers.d/90-app-ops-acceptance
sudo -k
sudo -n true && echo "passwordless sudo ok"
```

This file is removed in [After phase 11](#after-phase-11-remove-passwordless-sudo).

## Phase 4: Initial standby bootstrap

Do the SSH check and the replication firewall rule from ACCEPTANCE.md phase 4.
Skip its Ansible inventory steps. app-ops uses the same key-based SSH with
`BatchMode=yes` and `StrictHostKeyChecking=yes`, so it never prompts and never
accepts an unknown host key. On the initial primary:

```bash
python3 -m app_ops --inventory initial.yaml preflight-standby
python3 -m app_ops --inventory initial.yaml bootstrap-standby
python3 -m app_ops --inventory initial.yaml replication-status
python3 -m app_ops --inventory initial.yaml replication-status
```

The phase 4 PASS conditions apply unchanged. In addition, require:

- `preflight-standby` names the missing rule and stops if the replication
  firewall rule is absent. To test this, run it before adding the rule, or
  record that the rule was added first.
- `replication-status` reports `{"changed": false}` both times.
- Standby secrets reached the standby without being written to disk on the
  controller. The transfer is stdin to stdin: `find "$HOME/todo-operations" -newer SHA256SUMS -type f`
  lists only the two inventory files.

## Phase 5: Local DR tool

On the initial primary:

```bash
python3 -m app_ops --inventory initial.yaml install-dr-tool
python3 -m app_ops --inventory initial.yaml install-dr-tool
```

Require `{"changed": true}` and then `{"changed": false}`. On the standby, run
the `app_dr.py status` check from ACCEPTANCE.md phase 5.

For [Proxmox quarantine](PROXMOX-QUARANTINE.md), replace the playbook
commands with:

```bash
python3 -m app_ops --inventory initial.yaml install-quarantine-tool
```

The Guest Agent and SELinux opt-ins need the same separate approvals as there.
They are flags:

```bash
python3 -m app_ops --inventory initial.yaml install-quarantine-tool --enable-guest-exec
python3 -m app_ops --inventory initial.yaml install-quarantine-tool --enable-guest-exec --enable-selinux-entrypoint
```

Repeat the final command and require `{"changed": false}`. The helper is
`/opt/todo/bin/app-quarantine.sh`. Rehearse quarantine exactly as that guide
describes.

## Phase 6: Fence and promote

Unchanged. Promotion uses `app_dr.py` directly on the standby.

## Phase 7: Application failover

On the promoted host, trust app-ops as described in
[Trust app-ops on each controller](#trust-app-ops-on-each-controller). Write
`recovery.yaml` and add the client HTTPS firewall rule from ACCEPTANCE.md phase
7. Then:

```bash
python3 -m app_ops --inventory recovery.yaml deploy-promoted-application
python3 -m app_ops --inventory recovery.yaml deploy-promoted-application
```

Require `{"changed": true}`, then `{"changed": false}` in place of the
`changed=0` recap. This command only runs on the promoted host itself. From
any other controller it refuses before it changes anything. The rest of phase
7 is unchanged.

## Phase 8: Backup and isolated PITR

```bash
python3 -m app_ops --inventory recovery.yaml configure-backup
python3 -m app_ops --inventory recovery.yaml configure-backup
```

Require `{"changed": true}`, then `{"changed": false}`. The `app_backup.py`
steps in phase 8 are unchanged.

## Phase 9: Rebuild old primary as standby

Do the quarantine, firewall and SSH steps from ACCEPTANCE.md phase 9. The
promoted host needs key-based SSH to the old primary with a verified host key.
Then, on the promoted host, first prove the confirmation gate. Wrong
confirmations must refuse before anything on the rebuild host changes:

```bash
python3 -m app_ops --inventory recovery.yaml preflight-standby-rebuild \
  --confirm-fenced todo-primary --confirm-reseed todo-primary; echo "exit=$?"
```

Require exit 1 and a message saying both exact confirmations are required.
Then run the real read-only preflight:

```bash
python3 -m app_ops --inventory recovery.yaml preflight-standby-rebuild \
  --confirm-fenced "todo-primary is fenced" --confirm-reseed todo-primary
```

Require `{"changed": false}`. Only after that, with explicit reseed approval:

```bash
python3 -m app_ops --inventory recovery.yaml rebuild-standby \
  --confirm-fenced "todo-primary is fenced" --confirm-reseed todo-primary
python3 -m app_ops --inventory recovery.yaml cluster-status
```

`rebuild-standby` first checks `sudo -n` on both VMs, then repeats every
preflight gate. It publishes the redundancy configuration on the current
primary, then requires a TCP connection from the rebuild host to every
replication port; `replication port ... is not reachable` means a firewall
step above is missing, and nothing was deleted. Only then it reseeds the
whole group on the rebuild host, installs `app_dr.py`
there and waits for streaming. A failure at any point stops the run and
prints the host, command and error. Never rerun it blindly; follow
ACCEPTANCE.md phase 9.

Read the `cluster-status` JSON in place of the playbook output. For every
database, require the primary writable and the standby in recovery, with
streaming async, an active slot and zero lag. The rest of phase 9 is unchanged.

## Phases 10 and 11

Run `python3 -m app_ops --inventory recovery.yaml cluster-status` from the
current primary wherever those phases say `cluster-status.yml`. The JSON holds
the lag and LSNs to inspect. As with the playbook, exit 0 alone proves nothing.

## After phase 11: remove passwordless sudo

On both VMs:

```bash
sudo rm /etc/sudoers.d/90-app-ops-acceptance
sudo -k
sudo -n true; echo "exit=$?"
```

Require a non-zero exit. Record the removal. A run that leaves the file in
place is not complete.

## Verdict

Use the ACCEPTANCE.md verdicts. CLEAN PASS for app-ops also requires:

- every playbook step in the table above was run with app-ops, and no Ansible
  command was run;
- no password was typed into or read by app-ops;
- every refusal check above failed as described and changed nothing;
- every repeat reported `{"changed": false}`;
- passwordless sudo was removed from both VMs.
