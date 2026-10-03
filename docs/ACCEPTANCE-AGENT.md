# Acceptance run by an autonomous agent

This document lets a coding agent run the full two-VM acceptance in
[ACCEPTANCE.md](ACCEPTANCE.md) with as little operator typing as possible.
It adds **how** the agent operates (Proxmox API token, passwordless lab sudo,
evidence, stop rules) on top of the canonical **what** in ACCEPTANCE.md.
The operations tool is `app-ops` (plain SSH, [deploy/dr](../deploy/dr/README.md));
C9.13 says how the agent handles its sudo, trust and inventories.
For a change that cannot affect DR, the 20-minute
[quick acceptance](ACCEPTANCE-QUICK.md) on one VM is enough; it uses Part A's
preparation but not the rest of this document.

- Part A: one-time operator preparation.
- Part B: the kickoff message the operator pastes to the agent.
- Part C: the agent's instructions. The agent follows Part C literally.

If this document and ACCEPTANCE.md disagree on a safety gate, the stricter rule
wins. If either disagrees with the App registry
(`deploy/installer/app_installer/apps.py`) on a name, port or service, the
registry wins and the agent records the drift.

---

## Part A — Operator preparation (once, before the first agent run)

You do not have to work through Part A before starting the agent. You can paste
the kickoff message (Part B) straight away. The agent first runs the readiness
check (C1a), does everything it can itself, and then gives you **one** request
with ready-made commands and scripts for whatever is still missing. Part A is
the reference for what those commands do and why.

### A1. Proxmox API token scoped to the two lab VMs

In the **Proxmox node Shell**, check the version first:

```bash
pveversion
```

Create a role. On Proxmox VE 8:

```bash
pveum role add TodoAcceptance --privs "VM.Audit VM.PowerMgmt VM.Config.Network VM.Config.Options VM.Snapshot VM.Snapshot.Rollback VM.Monitor"
```

On Proxmox VE 9 (`VM.Monitor` was replaced by Guest Agent privileges):

```bash
pveum role add TodoAcceptance --privs "VM.Audit VM.PowerMgmt VM.Config.Network VM.Config.Options VM.Snapshot VM.Snapshot.Rollback VM.GuestAgent.Audit VM.GuestAgent.Unrestricted"
```

If `pveum` rejects a privilege name, remove only that name and tell the agent
which one is missing. Then create the user and token and grant access to the
two lab VMs only, plus read-only audit access for firewall/HA/task status.
Replace `107` and `108` with the real VM IDs:

```bash
pveum user add acceptance@pve --comment "Todo acceptance agent (lab only)"
pveum aclmod /vms/107 --users acceptance@pve --roles TodoAcceptance
pveum aclmod /vms/108 --users acceptance@pve --roles TodoAcceptance
pveum aclmod / --users acceptance@pve --roles PVEAuditor
pveum aclmod /sdn --users acceptance@pve --roles PVESDNUser
pveum user token add acceptance@pve agent --privsep 0
cat /etc/pve/pve-root-ca.pem
```

`PVESDNUser` on `/sdn` grants `SDN.Use`. On Proxmox VE 8/9, even a plain Linux
bridge NIC is modeled as an SDN zone (`localnetwork` by default), and any
`VM.Config.Network` change (the agent's `nic` action, used for quarantine
link-down/up and firewall flags) fails with `Permission check failed
(/sdn/zones/<zone>/<bridge>, SDN.Use)` without it. If `pveum` reports
`PVESDNUser` does not exist, create it: `pveum role add PVESDNUser --privs SDN.Use`.

The token command prints the secret **once**. Guest Agent execution through
this token is effectively root inside those two VMs; that is intended for this
disposable lab. Revoke later with `pveum user token remove acceptance@pve agent`.

VM-level firewall rules only take effect while the **node's own** firewall is
enabled — a separate switch from the datacenter-wide one the agent checks in
C9.6, and from each VM's own `enable` flag. Turn it on now, once, so the
agent's quarantine rehearsal does not stall on it:

```bash
pvesh set /nodes/<node>/firewall/options -enable 1
systemctl enable --now pve-firewall
```

The agent's tooling only reads Proxmox settings (`C2` Rule 6: it never enables
the datacenter or node firewall itself), so this must be done here, ahead of time.

### A2. Token file on the client/build host (the agent's machine)

Save the printed CA as `~/.config/todo-acceptance/pve-root-ca.pem`, then create
`~/.config/todo-acceptance/pve.env` with mode `0600` (never inside the Git
checkout):

```text
PVE_HOST=<proxmox address or name that matches its certificate>
PVE_NODE=<node name, as shown in the Proxmox GUI>
PVE_TOKEN_ID=acceptance@pve!agent
PVE_TOKEN_SECRET=<secret printed once by pveum>
PVE_CA=/home/<you>/.config/todo-acceptance/pve-root-ca.pem
```

```bash
chmod 700 ~/.config/todo-acceptance
chmod 600 ~/.config/todo-acceptance/pve.env
```

### A3. Passwordless sudo for the service user, inside the clean snapshots

Shortcut: after A1 and A2, `deploy/scripts/lab/prepare-agent-snapshots.sh` does
this whole step for both VMs through the token (rollback to the existing clean
snapshots, `ssh-copy-id`, the sudoers file, shutdown, snapshot `clean-agent`,
start). It asks for your VM password and sudo password. The manual steps are:

The agent cannot type a sudo password. On **both** lab VMs, starting from the
current clean pre-install snapshots, add a lab-only sudoers rule, then take new
clean snapshots so every reset keeps it:

```bash
echo 'gunstein ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/90-todo-acceptance
sudo chmod 0440 /etc/sudoers.d/90-todo-acceptance
sudo visudo -c
sudo -k; sudo -n true && echo PASSWORDLESS-SUDO-OK
```

Shut the VM down cleanly and take a snapshot in Proxmox (for example
`clean-agent`). The VM must otherwise be the documented clean baseline: no Todo,
Notes or Keycloak state. Also confirm in the VM options that **QEMU Guest Agent**
is enabled. Remove the file after the lab is retired. The agent's examples use the snapshot
name `clean-agent`; if you choose another name, put it in the kickoff message.

### A4. Client/build host prerequisites

The agent runs on the client/build host (the ThinkPad). It needs: the Git
checkout of this repository, rootless Podman (image builds), Python 3 with
`venv`, Jinja2 and PyYAML (`python3-jinja2`, `python3-yaml`: the builds render
every file here, so the VMs need neither for the install), `openssl`, `certutil`
(`libnss3-tools`), OpenSSH with keys already
trusted by both VMs (`ssh gunstein@<ip> hostname` works without a password),
and internet access for the Playwright Chromium download.
The readiness check asks the `python3` the build scripts find on `PATH`, not
only the one it runs in: run 28 stopped in phase 2 because the agent's shell
found another Python without PyYAML. Run the agent in a shell without an
activated virtualenv.

Decide whether the agent may use `sudo` on the client for `/etc/hosts` and CA
trust (`CLIENT_SUDO`). If not, the agent will ask you to run exactly one
prepared command at two moments (initial deployment and failover).

### A5. Readiness check on the client

From the repository root on the client/build host, run the read-only check.
It changes nothing: local checks, Proxmox API GET requests and read-only SSH
commands. Pass the kickoff values if they differ from the lab defaults:

```bash
python3 deploy/scripts/lab/acceptance_preflight.py --snapshot clean-agent --revision "$(git rev-parse HEAD)"
```

Fix every `FAIL` before starting the agent. `WARN` lines need a look but may be
acceptable; for example, the running VMs may still hold state from an earlier
run, because the agent resets them to the clean snapshots first.

---

## Part B — Kickoff message (fill in and paste to the agent)

```text
Run the Todo/Notes two-VM acceptance. Follow docs/ACCEPTANCE-AGENT.md Part C
exactly, with docs/ACCEPTANCE.md as the phase sequence.

Mode: NEW clean run
Revision to test: <full 40-char commit SHA on feature/podman-kube>
CI on that revision: <green | red | unknown>
Run ID: <e.g. 2026-09-25-agent-1>

Topology:
  initial primary: todo-primary, 192.168.0.102, VMID 107, clean snapshot <name>
  initial standby: todo-standby, 192.168.0.108, VMID 108, clean snapshot <name>
  client/build host source IPv4 as seen by the VMs: 192.168.0.100
  service user on both VMs: gunstein
  Proxmox env file: ~/.config/todo-acceptance/pve.env
  CLIENT_SUDO: <yes only if 'sudo -n true' works on the client | no>

Pre-approvals (durable for this run only; every STOP rule still applies):
  [x] Reset both VMs to the clean snapshots above (destroys their current state)
  [x] Guest Agent guest-exec opt-in and SELinux helper entrypoint opt-in on VM 107
  [x] Quarantine rehearsal with a brief outage of VM 107 before promotion
  [x] Fence VM 107 and promote the standby database group
  [x] Destructive reseed of VM 107's three databases after verified backup/PITR
  [x] Cleanup of the disposable PITR restore containers/volumes
  [x] One nightly backup run on VM 107 (03-12a) and the restore of VM 107's
      three databases from it (03-12c), which discards the row written after it
  [x] One nightly backup run on VM 108 (08-16), which also deletes archived
      WAL older than the oldest kept base backup
  [x] Store the generated testuser password in a 0600 tmpfs file for this run
Not approved: anything else destructive, any source change, commit or push,
and any change on the client outside the run folder (do not stop, start or
reconfigure services or processes there; report a blocker instead).

Run every C9 step with `$A step NAME`, one step at a time, and read its output
before the next. Never type a step's command line yourself. A step that ends
in STOP or REFUSED is a STOP at once.
```

---

## Part C — Agent instructions

### C1. Your job, in one paragraph

You execute the eleven phases of `docs/ACCEPTANCE.md` on two Proxmox lab VMs,
in order, on one clean Git revision, and you collect evidence for every phase.
You work alone: you use SSH to the VMs, the Proxmox API through
`deploy/scripts/lab/pve_lab.py`, and passwordless sudo inside the VMs. You do
as much as possible yourself. You ask the operator only in the cases listed in
C4, as seldom as possible, and always in the form C4a describes. You never
change source code. At the end you write a verdict. Being careful is more
important than being fast.

### C1a. Start: readiness first, then at most one operator request

Before phase 1, and before anything that changes a VM:

1. Create the run folder (C5). If `git status --porcelain` is empty but `HEAD`
   is not the kickoff revision, run `git fetch origin` and
   `git checkout --detach <revision>`; that is not a source change. A dirty
   tree is a STOP.
2. Set `RUN` as in C9.1 and run the read-only readiness check exactly like
   this, with the kickoff values. It appends every attempt, with its time and
   exit status, to `logs/00-readiness.log`; the report needs that log, and
   `$A step` refuses the first step of phase 1 until its last attempt ends
   with `READY for the agent run.` and `exit=0` (runs 21 and 34 ran the check
   in the terminal only, and were not clean):

   ```bash
   { echo "# start $(date --iso-8601=seconds)"; python3 deploy/scripts/lab/acceptance_preflight.py --snapshot clean-agent --revision "$(git rev-parse HEAD)" --primary 192.168.0.102 --standby 192.168.0.108 --primary-vmid 107 --standby-vmid 108 --user gunstein --client-ip 192.168.0.100; echo "exit=$?"; } >> "$RUN/logs/00-readiness.log" 2>&1; tail -n 20 "$RUN/logs/00-readiness.log"
   ```

   Also run `sudo -n true` on the client. If `CLIENT_SUDO: yes` but that fails,
   continue as `CLIENT_SUDO: no` and record it. You cannot type a password.
3. Fix yourself whatever you can with your own access: a missing client SSH key
   (`ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519`), the virtual
   environments and Python packages, and client packages when client sudo works
   without a password.
4. For each remaining FAIL, add the operator step from the table below to
   **one** request (C4a). Order the steps so that each one works: Proxmox node
   Shell first, then the token file, then the snapshots. Fill in every real
   value: VM IDs, addresses, node name, Proxmox version and existing snapshot
   names. Read snapshot names with
   `pve_lab.py get /nodes/{node}/qemu/<VMID>/snapshot` once the token works.
   If the token does not work yet, give the operator the command that shows
   them: `qm listsnapshot <VMID>` in the node Shell.
5. After "done", run the same readiness line again (it appends). If a FAIL remains, send one
   follow-up request that covers only what is still missing, with the error.
   Start phase 1 only when nothing FAILs.

| Readiness FAIL | Operator step you prepare |
|---|---|
| Token file, Proxmox CA, API access, token privileges, Guest Agent exec privilege, `SDN.Use` | Node Shell block from A1 with the real VM IDs. Include only the role line for the operator's Proxmox version. If you cannot read the version yet, include `pveversion` and both lines, and say which to use. Then a ThinkPad script that writes `pve.env` and the CA file (template below). |
| Node firewall disabled | Node Shell: `pvesh set /nodes/<node>/firewall/options -enable 1` and `systemctl enable --now pve-firewall`. |
| Datacenter firewall disabled | Node Shell: `pvesh get /cluster/firewall/options`, then `pvesh set /cluster/firewall/options -enable 1`. Say that Proxmox then blocks incoming traffic to the Proxmox host except the web GUI (8006) and SSH (22) from its local network. The operator decides; do not enable it yourself (C2 rule 6). |
| QEMU Guest Agent not enabled on a VM | Node Shell: `qm set <VMID> --agent enabled=1`, **before** the snapshot step, so that the new snapshot contains it. |
| Snapshot `clean-agent` missing, passwordless sudo missing, Jinja2/PyYAML missing, or client SSH key not accepted | ThinkPad, in the checkout: `bash deploy/scripts/lab/prepare-agent-snapshots.sh 107:192.168.0.102:<existing clean snapshot> 108:192.168.0.108:<existing clean snapshot>`. Say that it destroys the current state of both VMs, and that it asks for each VM's login password and sudo password. |
| `clean-agent` exists but a guest check still fails | STOP and ask. The snapshot is not the documented baseline, and deleting a snapshot is the operator's decision. |
| Client tool missing and no client sudo | ThinkPad: one `sudo apt-get install -y ...` line with exactly the missing packages (`podman`, `libnss3-tools`, `python3-venv`). |
| Hostname or VM identity mismatch | STOP: wrong VM or wrong kickoff values. |

Record WARN lines, but do not ask about them unless C4 lists them.

Token file script. Write it to the run folder's `operator/` directory and show
its full content in the request. The secret is typed only into `read -s`:

```bash
#!/usr/bin/env bash
set -euo pipefail
dir="$HOME/.config/todo-acceptance"
install -d -m 700 "$dir"
read -rp "Proxmox address (as in its certificate): " host
read -rp "Proxmox node name (as in the GUI): " node
read -rsp "Token secret printed by pveum: " secret; echo
echo "Paste the CA printed by 'cat /etc/pve/pve-root-ca.pem', then press Ctrl-D:"
cat > "$dir/pve-root-ca.pem"
( umask 077; printf 'PVE_HOST=%s\nPVE_NODE=%s\nPVE_TOKEN_ID=acceptance@pve!agent\nPVE_TOKEN_SECRET=%s\nPVE_CA=%s\n' \
    "$host" "$node" "$secret" "$dir/pve-root-ca.pem" > "$dir/pve.env" )
echo "Wrote $dir/pve.env"
```

### C2. Absolute rules (never break these)

1. **Never** edit, commit or push files in the Git checkout. A source change
   makes the run REPAIRED, not CLEAN. If something in the repository is wrong,
   STOP and report it.
2. **Never** print, log or store a secret except where C6 says so. Secrets are:
   the Proxmox token, the Keycloak admin password, the testuser password and any
   `podman secret` value. Never run `set -x`. Never `cat` `pve.env`.
3. **Never** run a command that changes state a second time after it failed,
   timed out or gave an unexpected result. That covers installers
   (`install.sh`, app-ops commands), `app_dr.py` and `app_backup.py` actions
   other than `status`, `firewall-cmd` changes, Proxmox power, NIC and firewall
   changes, SQL writes, marker creation and every rehearsal sub-step. It is
   worst for one-shot commands such as `failover`, `rebuild-standby`,
   snapshot rollback, volume deletion, `app_backup.py restore --replace`,
   `cleanup-restore` and `app_installer backup restore`. Inspect state instead (C8). A read-only check is not
   covered: C8 says when it may be repeated.
4. **Never** disable or weaken SELinux, fapolicyd, firewalld, SSH host-key
   checking or TLS verification. Never use `curl -k`, `--insecure`,
   `E2E_IGNORE_HTTPS_ERRORS=true`, `StrictHostKeyChecking=no` or
   `ignore_https_errors=True`.
5. **Never** skip a read-only preflight, and never reboot both VMs at the same time.
6. **Never** enable the Proxmox datacenter or node firewall yourself.
7. **Never** change or delete a Proxmox firewall rule you did not create in this
   run. Identify rules by their `comment`, and read the full rule before you
   change or delete it; never act on a rule position alone.
8. A failed check means **STOP** (C3). A passing command exit code alone is not
   a passing check: compare the output with the expected values.
9. **Never** run `install.sh` or `app_installer install` after
   phase 3. On a host with replication, a new install rewrites the database
   units without their LAN publication and silently cuts off the standby.
10. **Never** start, stop or restart services or pods by hand
    (`systemctl --user start|stop|restart`, `podman kube play`, `podman start`)
    unless a C9 step tells you to run exactly that command. Services come back
    through the documented reboot or tool, never through an improvised command.
11. **Never** change Proxmox state (power, `nic`, firewall options or rules)
    outside the C9 step that says so, and never skip a step of a sequence
    that changed Proxmox state: the later steps are what undo it.
12. Do not act on a theory. When something does not match the guide, record
    your observations and diagnosis, then STOP (C3). Fixing things is the
    operator's decision.
13. **Never** type a C9 step's command line yourself, and never run two steps
    in one command. Run each step alone with `$A step NAME` (C9.1), read what
    it prints, and only then run the next.

### C3. What STOP means

1. Do not run any further command that changes anything (VMs, Proxmox, client).
2. Keep fencing and quarantine exactly as they are.
3. Run read-only diagnostics only (status, logs, `journalctl`, `podman ps -a`).
4. Write `Verdict: BLOCKED` plus the phase, the exact command, its full output
   and your diagnosis in the run record.
5. Report to the operator: what failed, what state everything is in now, and
   what you propose. Then wait. Use `docs/ACCEPTANCE-TROUBLESHOOTING.md` only
   to propose a next step; do not execute a recovery without the operator's
   explicit go-ahead, except the non-destructive waits listed there.

### C4. When to ask the operator (the only cases)

- Before phase 1: whatever the readiness check (C1a) finds missing that you
  cannot fix yourself, in one request.
- A STOP condition (C3).
- `pve_lab.py` returns HTTP 401/403, or either the datacenter firewall
  (`get /cluster/firewall/options`) or the node's own firewall
  (`get /nodes/{node}/firewall/options`) shows `enable: 0` or no `enable`: VM
  firewall rules would have no effect either way. Ask; do not enable either
  yourself (A1 should have enabled the node firewall ahead of time).
- `CLIENT_SUDO: no` and client `/etc/hosts` or CA trust must change (phase 3
  and phase 7). Give the operator the prepared script from C9.4 and wait for
  "done". Then verify the result yourself. Say in the C1a request that these
  two moments will come.
- Anything the kickoff message did not pre-approve.

Otherwise do not ask. Report progress briefly at the end of each phase.

### C4a. How to ask the operator

The operator should be able to act without reading any other document. Every
request uses this form:

```text
OPERATOR ACTION <n> (about <m> minutes)
Why: <one sentence>
Where: <exact place: ThinkPad terminal in the checkout | Proxmox node Shell
       (web GUI: Datacenter > <node> > Shell) | a named web GUI page>
Destructive: <what is destroyed, or "nothing">
Steps:
  1. <complete command or script path, with every real value filled in>
  2. ...
You should see: <expected output per step>
Reply: "done", or paste the lines that start with <...>. Never paste passwords
or tokens.
Next I will: <what you do afterwards>
```

- Put everything the operator must do at that moment into one request, in the
  order to run it. Never send one question at a time.
- Commands must be complete and ready to paste: no `<placeholders>`, no
  "adjust as needed", no shell prompts such as `$` or `#`.
- If a ThinkPad step is longer than about five lines, write it as a script in
  `~/todo-acceptance-runs/<RUN_ID>/operator/NN-<name>.sh` (outside the
  checkout), show its full content in the request, and ask the operator to run
  `bash <path>`. The Proxmox node Shell cannot read ThinkPad files, so give
  node Shell steps as one paste block.
- Secrets are typed only into `read -s` prompts or an editor, never into chat
  or command arguments.
- Never ask for something you can do with your own access, and never ask the
  operator to copy output that you can read yourself. Afterwards, check the
  result yourself.

### C5. Tools you use

**Run folder and the acceptance tool.** Every step of C9 writes into
`~/todo-acceptance-runs/<RUN_ID>/` (outside the checkout): the checks and
state changes around the product through `deploy/scripts/lab/acceptance.py`
(`$A`), the product's own commands through the `product`, `vm` and `ops`
helpers; you run every step with `$A step NAME` (C9.1). Both put each step's
full output and exit status in its own `logs/<step>...log`; `acceptance.py` also appends a line to `record.jsonl` and
builds `REPORT.md` from it at the end. Keep short notes in `run-record.md`
there as you go (what you did, any STOP).

**Proxmox API.** Always use the helper from the checkout; never write your own
curl commands with the token:

```bash
python3 deploy/scripts/lab/pve_lab.py get /nodes/{node}/qemu/107/status/current
python3 deploy/scripts/lab/pve_lab.py get /nodes/{node}/qemu/107/config
python3 deploy/scripts/lab/pve_lab.py task /nodes/{node}/qemu/107/snapshot/clean-agent/rollback
python3 deploy/scripts/lab/pve_lab.py task /nodes/{node}/qemu/107/status/start
python3 deploy/scripts/lab/pve_lab.py task /nodes/{node}/qemu/107/status/shutdown
python3 deploy/scripts/lab/pve_lab.py task /nodes/{node}/qemu/107/status/stop
python3 deploy/scripts/lab/pve_lab.py task /nodes/{node}/qemu/107/status/reboot
python3 deploy/scripts/lab/pve_lab.py post /nodes/{node}/qemu/107/agent/ping
python3 deploy/scripts/lab/pve_lab.py exec 107 -- /opt/todo/bin/app-quarantine.sh check todo-primary gunstein
python3 deploy/scripts/lab/pve_lab.py nic 107 link_down 1
python3 deploy/scripts/lab/pve_lab.py set /nodes/{node}/qemu/107/config onboot=0
python3 deploy/scripts/lab/pve_lab.py get /nodes/{node}/qemu/107/firewall/options
python3 deploy/scripts/lab/pve_lab.py get /nodes/{node}/qemu/107/firewall/rules
python3 deploy/scripts/lab/pve_lab.py get /cluster/firewall/options
python3 deploy/scripts/lab/pve_lab.py get /cluster/ha/resources
python3 deploy/scripts/lab/pve_lab.py fence 107
bash deploy/scripts/lab/ports-closed.sh 192.168.0.102 22 5432 5433 5434 8443
ssh gunstein@192.168.0.108 'bash -s' -- 192.168.0.102 22 5432 5433 5434 8443 < deploy/scripts/lab/ports-closed.sh
```

`exec` prints `exited`, `exitcode`, `out-data` and `err-data` and exits with the
guest command's exit code. Exit code `124` means the guest command did not
finish: do not repeat it; poll
`get "/nodes/{node}/qemu/107/agent/exec-status?pid=<PID>"` instead.
`task` waits and fails unless the Proxmox task ends with `OK`.
`nic` changes one flag on every network device and keeps MAC and bridge.
`fence` is the whole fencing step in one command: it refuses if HA manages the
VM, stops it, sets `onboot=0` and `link_down=1` on every `netN`, reads it all
back and prints that as evidence; it fails unless the VM is fenced.
`ports-closed.sh` prints each port as `open`, `timeout` or `closed` with the
error (`Connection refused`, `No route to host`) and exits
1 if any is open. It needs only bash, so it also runs on a VM through SSH.

**Guests.** SSH as `gunstein` with host-key checking on. Use `sudo -n` for root
commands inside the VMs so a missing sudo rule fails immediately instead of
hanging. Run app-ops as C9.13 says; it uses `sudo -n` only and never asks
for a password. Never run `ansible` or `ansible-playbook`: Ansible is retired.

**Long commands.** The bundle build, `bootstrap-standby`, `failover` and
`rebuild-standby` can take more than ten minutes. Their lines end in `&`, so
`$A step` starts them in the background with their log. Read the log until
the `exit=` line appears; the next `$A step` refuses until then. If your own
tool times out while waiting, the command is still running: read the log,
never start it a second time.

**Interactive prompts in ACCEPTANCE.md.** Replace `read -rp "Client IPv4..."`
style prompts for non-secret values with the value from the kickoff. For
secrets, use C6 instead of `read -rsp`.

**Waiting for the Guest Agent.** After a VM start, `post .../agent/ping`
fails until the agent is up. Repeat it every 10 seconds for up to 5 minutes;
that repetition is read-only and allowed.

**Waiting for a reboot.** Record the boot ID first
(`cat /proc/sys/kernel/random/boot_id`), reboot through the API, then poll SSH
every 10 seconds for up to 10 minutes until the boot ID differs.

**Waiting until the workloads are up.** A systemd unit is `active` as soon as
its pod starts; containers, health checks and HTTP answers follow seconds
later. `$A check services` and `$A do reboot` wait for that with
`deploy/scripts/wait-ready.sh` (`app` on a host with the application,
`standby` on a database-only standby), up to 5 minutes. Do not write your own
wait loops around `systemctl` or `podman`. A failed `todo-dr-check.service`
(the scheduled DR check) does not fail `check services`: it fails on purpose
while DR is degraded, as after the failover until the rebuild, and
`$A check monitor` tests it on its own.

### C6. Secrets handling (the only allowed patterns)

- Testuser password: generate once in phase 3 into tmpfs, never into the run folder:

  ```bash
  install -d -m 700 "$XDG_RUNTIME_DIR/todo-acceptance"
  ( umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$XDG_RUNTIME_DIR/todo-acceptance/e2e-password" )
  ```

  Use it only as `E2E_PASSWORD="$(cat "$XDG_RUNTIME_DIR/todo-acceptance/e2e-password")"`
  inside a subshell. Delete the file at the end of the run.
- Keycloak admin password: never leaves process memory on the client. Read it
  over SSH directly into an environment variable inside one subshell
  (`SERVING_IP` is the current serving host):

  ```bash
  KEYCLOAK_ADMIN_PASSWORD="$(ssh gunstein@"$SERVING_IP" "podman secret inspect --showsecret --format '{{.SecretData}}' keycloak-admin-password")"
  ```

- Proxmox token: only `pve_lab.py` reads it.
- If a secret appears in any output by accident: STOP, tell the operator which
  file or log contains it, and do not copy it anywhere else.

### C7. Evidence you must capture (do not skip; earlier runs lacked this)

The evidence is what C9 writes: one log per step, `record.jsonl` and
`REPORT.md`. `acceptance.py` records the values itself (boot IDs, roles, CA
fingerprints, marker IDs, TLS rows, Proxmox state, disk sizes); the product
logs hold every app-ops and DR command's JSON line and exit status. Do not
copy values by hand. In `run-record.md`, write only what the logs cannot
show: where you stopped and why, what the operator did (client trust,
approvals), and every deviation from ACCEPTANCE.md.

Environment deviations that are expected in an agent run and must be recorded,
but do not by themselves downgrade the verdict: the lab sudoers file from A3
instead of the run-only file in ACCEPTANCE.md, and the skipped refusal check
that needs sudo without NOPASSWD (C9.13); Proxmox API token instead of the node
Shell; the testuser password in a tmpfs file; firewall evidence from API rule
listings plus connection tests instead of `pve-firewall` output.

### C8. If a command fails or times out

1. Do not run it again.
2. Save the complete output.
3. Check the current state read-only (service status, database role, Proxmox
   task list `get /nodes/{node}/tasks?vmid=107`, `podman ps -a`).
4. Only read-only commands, waits for asynchronous work (fapolicyd refresh,
   replication catching up, Keycloak starting after boot) and re-running a
   read-only check may be repeated. A `$A check` that failed because
   something was still starting may run once more after
   `$A check services` passes; both results are in `record.jsonl` and
   `REPORT.md` lists the repeat. It does not change the verdict. Everything that changes
   state: STOP (C3), never run it again (C2 rule 3).
5. Look up the symptom in `docs/ACCEPTANCE-TROUBLESHOOTING.md` and put its
   "safe next observation" in your report. Do not carry out a recovery
   yourself, even one that looks obvious.

### C9. Phase-by-phase instructions

The phases are those of `docs/ACCEPTANCE.md`; read each one there before you
start it, for the why. What you run is the fixed list of commands below, in
order, each with `$A step NAME`: C9.1 explains how, C9.2 to C9.11 are the
phases. Values use the lab defaults (`.102` = VM 107 = `todo-primary`, `.108`
= VM 108 = `todo-standby`, client `.100`); if the kickoff values differ, STOP
and ask.

#### C9.0 Before phase 1

1. Read `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/ACCEPTANCE.md`,
   `docs/PROXMOX-QUARANTINE.md`, `docs/ACCEPTANCE-TROUBLESHOOTING.md`,
   `deploy/dr/README.md` and C9.13.
2. `git status --porcelain` must be empty and `git rev-parse HEAD` must equal the
   kickoff revision. Otherwise STOP.
3. If CI is not `green`, run the local suite and record the result; STOP on failure:

   ```bash
   python3 -m venv ~/todo-acceptance-runs/$RUN_ID/venv
   ~/todo-acceptance-runs/$RUN_ID/venv/bin/pip install jinja2 PyYAML
   PATH=~/todo-acceptance-runs/$RUN_ID/venv/bin:$PATH python -m unittest discover --start-directory tests
   ```

4. Print the registry and compare it with the table in ACCEPTANCE.md
   ("Registered workload group"); record any drift:

   ```bash
   PYTHONPATH=deploy/installer python3 -m app_installer replication-apps --details
   ```

5. Check the Proxmox helper and access: `get /version`, both VMs'
   `status/current` and `config`, `get /cluster/firewall/options`,
   `get /cluster/ha/resources`. `01-3 do rollback` records VM 107's `onboot`
   from its clean snapshot; phase 9 restores that value.

#### C9.1 How you run the steps

Every line below that starts with `$A --step`, `vm`, `ops` or `product` is one
step, and its name is the word after that: `06-3` for
`$A --step 06-3 do fence 107`, `06-6-preflight` for `vm 06-6-preflight ...`.
You never type those lines. You run each step by its name, from the
repository root on the client, in one shell where you first set:

```bash
RUN_ID="<run ID from the kickoff>"
RUN=~/todo-acceptance-runs/$RUN_ID
mkdir -p "$RUN/logs"
A="python3 deploy/scripts/lab/acceptance.py --run $RUN_ID"
```

and then, one step at a time, in the order of this guide:

```text
$A step 01-1
$A step 01-2
...
```

`$A step NAME` (`deploy/scripts/lab/acceptance.py`) reads the line from this
guide in the checkout and runs it exactly as written, with the helpers from
`deploy/scripts/lab/helpers.sh`. It refuses (`REFUSED: ...`, exit 3, nothing
run) unless NAME is the next step of the guide and the step before it passed.
It ends with `STEP NAME: PASS` or `STEP NAME: STOP, <why>`; a STOP is a STOP
(C3). A step runs once; the one exception is C8 item 4, a failed `check` run
once more. So a changed command, a skipped step or a step after a failure
cannot run: that stopped runs 24 and 26.

What `step` checks for you:

- An `acceptance.py` line (`$A --step ...`): its `RESULT: PASS`. That tool runs
  the glue around the product the same way every time, writes its own log and
  a line in `record.jsonl`, and compares the result itself. It refuses to
  repeat a failed `do`; only the operator can allow that
  (`--operator-approved`).
- A product line (`product`, `vm`, `ops`, the product's own commands as in
  ACCEPTANCE.md): its log `logs/NAME.log` holds the start time, the exact
  command (`# command: ...`), the full output and the exit status. It must end
  with `exit=0`, or `exit=1` for a name ending in `-refused`, and print what
  the comment gives after `→`: that JSON line, or that word.

What you still check yourself, in the output `step` prints: everything else a
comment says (for example `must print nothing` or `zero failed archive
attempts`) and what the text under a block asks for. If it does not match:
STOP, even when `step` said PASS.

- Values are the lab defaults (`.102` = VM 107 = `todo-primary`, `.108` =
  VM 108 = `todo-standby`, client `.100`, snapshot `clean-agent`). `step` runs
  them as written; if the kickoff values differ, STOP and ask.
- A line ending in `&` (`failover`, `rebuild-standby`) takes several minutes:
  `step` starts it in the background and returns at once. Read its log until
  the `exit=` line appears; the next step refuses until then. Never start it
  twice (C5 "Long commands").
- The only other commands you type are the read-only ones in C9.0, the
  secret file in C6, the client trust in C9.4 (or the script you prepare for
  the operator), `rm` of the secret file and `$A report full` at the end
  (C9.11).

#### C9.2 Phases 1 and 2 — Clean hosts, build and stage (pre-approved reset)

```bash
$A --step 01-1 do proxmox-firewall 107 off     # an earlier run may have left it on
$A --step 01-2 do proxmox-firewall 108 off
$A --step 01-3 do rollback 107 clean-agent 192.168.0.102
$A --step 01-4 do rollback 108 clean-agent 192.168.0.108
$A --step 01-5 check clean-host 192.168.0.102
$A --step 01-6 check clean-host 192.168.0.108
vm 01-7-prerequisites-102 192.168.0.102 'sudo -n dnf install -y python3-jinja2 python3-pyyaml'
vm 01-8-prerequisites-108 192.168.0.108 'sudo -n dnf install -y python3-jinja2 python3-pyyaml'
product 02-1-build-offline deploy/offline/build-bundle.sh &   # wait for exit=
product 02-2-build-operations deploy/scripts/build-operations-package.sh
product 02-3-transfer-102 scp dist/todo-offline-m12.tar.gz dist/todo-offline-m12.tar.gz.sha256 dist/todo-operations.tar.gz dist/todo-operations.tar.gz.sha256 gunstein@192.168.0.102:
product 02-4-transfer-108 scp dist/todo-offline-m12.tar.gz dist/todo-offline-m12.tar.gz.sha256 dist/todo-operations.tar.gz dist/todo-operations.tar.gz.sha256 gunstein@192.168.0.108:
vm 02-5-verify-102 192.168.0.102 'sha256sum -c todo-offline-m12.tar.gz.sha256 todo-operations.tar.gz.sha256 && tar -xzf todo-offline-m12.tar.gz && tar -xzf todo-operations.tar.gz && (cd todo-offline-m12 && sha256sum --quiet -c SHA256SUMS && cat VERSION) && (cd todo-operations && sha256sum --quiet -c SHA256SUMS && cat VERSION)'
vm 02-6-verify-108 192.168.0.108 'sha256sum -c todo-offline-m12.tar.gz.sha256 todo-operations.tar.gz.sha256 && tar -xzf todo-offline-m12.tar.gz && tar -xzf todo-operations.tar.gz && (cd todo-offline-m12 && sha256sum --quiet -c SHA256SUMS && cat VERSION) && (cd todo-operations && sha256sum --quiet -c SHA256SUMS && cat VERSION)'
```

Both `VERSION` files on both VMs must show the kickoff revision and
`source_state=clean`. Installing `python3-jinja2` is a documented target
prerequisite, not a source change: record it as an expected deviation.

#### C9.3 Phase 3 — Initial deployment on `.102`

```bash
vm 03-1-trust 192.168.0.102 'cd ~/todo-offline-m12 && for source in "$PWD"/deploy/installer/app_installer/*.py; do source=$(realpath "$source"); sudo -n fapolicyd-cli --file update "$source" --trust-file app-installer || sudo -n fapolicyd-cli --file add "$source" --trust-file app-installer; done && sudo -n fapolicyd-cli --update'
vm 03-2-install 192.168.0.102 'cd ~/todo-offline-m12 && sh ./preflight.sh && sh ./install.sh --publish-address 192.168.0.102'   # → {"changed": true}
$A --step 03-3 do firewall-https 192.168.0.102 192.168.0.100
$A --step 03-4 check services 192.168.0.102 app
```

Client name resolution and CA trust for `.102`: C9.4 (the operator runs it
when `CLIENT_SUDO: no`). Then generate the testuser password (C6), and set up
the browser environment and the test user. `todo-backend/.venv` is ignored by
Git; `git status --porcelain` must stay empty. `provision-user.sh` forwards
port 8080 through one SSH tunnel and closes exactly that tunnel:

```bash
product 03-4a-browser-env sh -c 'python3 -m venv todo-backend/.venv && todo-backend/.venv/bin/python -m pip install -r todo-backend/requirements-e2e.txt && todo-backend/.venv/bin/python -m playwright install chromium'
product 03-4b-provision-user deploy/scripts/lab/provision-user.sh gunstein@192.168.0.102
$A --step 03-5 check ca 192.168.0.102
$A --step 03-6 check headers
$A --step 03-7 check browser
$A --step 03-8 do markers phase3
$A --step 03-9 check markers 192.168.0.102
$A --step 03-10 do reboot 107 192.168.0.102 app
$A --step 03-11 check ca 192.168.0.102
$A --step 03-12 check markers 192.168.0.102
```

The single host's nightly backup (B1): `install.sh` turned on
`todo-backup.timer`. Run it once, write one Todo row after it, restore the
three databases from that backup, and check that the row is gone while the
markers, written before the backup, remain:

```bash
$A --step 03-12a do backup-nightly 192.168.0.102
vm 03-12b-after-backup 192.168.0.102 "podman exec todo-postgres psql --username todo --dbname todo --set ON_ERROR_STOP=1 --command \"INSERT INTO todos (title, completed) VALUES ('written after the nightly backup', false);\""
vm 03-12c-restore 192.168.0.102 'cd ~/todo-offline-m12 && PYTHONPATH=deploy/installer python3 -m app_installer backup restore --confirm-restore todo-primary'   # → {"changed": true}; after one "restored" line each for todo, notes and keycloak
vm 03-12d-restored 192.168.0.102 "podman exec todo-postgres psql --username todo --dbname todo --set ON_ERROR_STOP=1 --command \"SELECT 1 / (CASE WHEN count(*) = 0 THEN 1 ELSE 0 END) AS row_gone FROM todos WHERE title = 'written after the nightly backup';\""   # division by zero if the row survived
$A --step 03-12e check services 192.168.0.102 app
$A --step 03-12f check markers 192.168.0.102
vm 03-13-install-again 192.168.0.102 'cd ~/todo-offline-m12 && sh ./install.sh --publish-address 192.168.0.102'   # → {"changed": false}
$A --step 03-14 check services 192.168.0.102 app
```

The trust log shows `update` errors for files not yet trusted, then a
successful `add`: expected.

#### C9.4 Client trust (phases 3 and 7)

With `CLIENT_SUDO: yes` (and `sudo -n true` working on the client), run these
yourself. With `CLIENT_SUDO: no`, write them as
`operator/<phase>-client-<ip>.sh`, with the first line set to the current
serving host (`.102` in phase 3, `.108` in phase 7). Ask as C4a describes, and
wait:

```bash
IP=192.168.0.102
sudo sed -i -e '/[[:space:]]todo\.test\([[:space:]]\|$\)/d' -e '/[[:space:]]notes\.test\([[:space:]]\|$\)/d' /etc/hosts && echo "$IP todo.test notes.test" | sudo tee -a /etc/hosts
deploy/scripts/lab/trust-serving-ca.sh "gunstein@$IP"
```

The test user (`03-4b`, C9.3) lives in the replicated Keycloak database and
survives failover; it is not provisioned again after promotion.

#### C9.5 Phase 4 — Trust, inventories and standby bootstrap

Before phase 4, on `.102` (C9.13): trust app-ops twice (`changed`, then
`unchanged`), write `initial.yaml`, record the skipped first sudo refusal.

```bash
vm 04-1-trust-ops 192.168.0.102 'cd ~/todo-operations && sha256sum --quiet -c SHA256SUMS && sudo -n sh deploy/scripts/trust-files.sh trust todo "$PWD"/deploy/dr/app_ops/*.py "$PWD"/deploy/installer/app_installer/*.py'   # → changed
vm 04-2-trust-ops-again 192.168.0.102 'cd ~/todo-operations && sudo -n sh deploy/scripts/trust-files.sh trust todo "$PWD"/deploy/dr/app_ops/*.py "$PWD"/deploy/installer/app_installer/*.py'   # → unchanged
vm 04-3-inventory 192.168.0.102 'cd ~/todo-operations && printf "%s\n" "user: gunstein" "hosts:" "  todo-primary: {role: primary, address: 192.168.0.102, local: true}" "  todo-standby: {role: standby, address: 192.168.0.108}" > initial.yaml && cat initial.yaml'
product 04-4-sudo-refusal-skip echo 'C9.13 item 1: first sudo refusal check skipped (lab sudoers)'
$A --step 04-5 do pin-ssh 192.168.0.102 192.168.0.108
$A --step 04-6 do pin-ssh 192.168.0.108 192.168.0.102
ops 04-7-preflight-refused 192.168.0.102 '--inventory initial.yaml preflight-standby'   # exit=1, message names the missing rich rule
$A --step 04-8 do firewall-replication 192.168.0.108 192.168.0.102 add
ops 04-9-preflight 192.168.0.102 '--inventory initial.yaml preflight-standby'           # → {"changed": false}
ops 04-10-bootstrap 192.168.0.102 '--inventory initial.yaml bootstrap-standby' &        # → {"changed": true}; wait for exit=
ops 04-11-status 192.168.0.102 '--inventory initial.yaml replication-status'            # → {"changed": false}
ops 04-12-status-again 192.168.0.102 '--inventory initial.yaml replication-status'      # → {"changed": false}
vm 04-13-no-secrets 192.168.0.102 'cd ~/todo-operations && find . -newer SHA256SUMS -type f'   # only initial.yaml
$A --step 04-14 check replication-tls 192.168.0.102
$A --step 04-15 check roles 192.168.0.108 standby
$A --step 04-16 do markers phase4
$A --step 04-17 check markers 192.168.0.108
$A --step 04-18 do reboot 108 192.168.0.108 standby
$A --step 04-19 check replication-tls 192.168.0.102
$A --step 04-20 check markers 192.168.0.108
```

#### C9.6 Phase 5 — DR tool and quarantine rehearsal (pre-approved outage)

```bash
ops 05-1-install-dr-tool 192.168.0.102 '--inventory initial.yaml install-dr-tool'          # → {"changed": true}
ops 05-2-install-dr-tool-again 192.168.0.102 '--inventory initial.yaml install-dr-tool'    # → {"changed": false}
vm 05-3-dr-status 192.168.0.108 'python3 /opt/todo/bin/app_dr.py status'                   # standby, not writable, 0 bytes lag, primary reachable
ops 05-4-install-quarantine-tool 192.168.0.102 '--inventory initial.yaml install-quarantine-tool --enable-guest-exec --enable-selinux-entrypoint'        # → {"changed": true}
ops 05-5-install-quarantine-tool-again 192.168.0.102 '--inventory initial.yaml install-quarantine-tool --enable-guest-exec --enable-selinux-entrypoint'  # → {"changed": false}
$A --step 05-6 check quarantine-ready 107 todo-primary
$A --step 05-7 do quarantine-profile 107 192.168.0.100 192.168.0.108
$A --step 05-7a check monitor 192.168.0.102 ok     # install-dr-tool turned the DR check timer on on both hosts
$A --step 05-7b check monitor 192.168.0.108 ok
```

The rehearsal. VM 107 is still the writable primary, so this brief outage is
harmless. The proofs run before services stop, because a blocked port only
proves something when a service listens behind it. Sub-steps 2 to 6 put VM 107
into quarantine; only 7 and 8 take it out again.

```bash
# 1. Baseline, firewall still off
$A --step 05-8-1a check connect client 192.168.0.102 8443 open
$A --step 05-8-1b check connect 192.168.0.108 192.168.0.102 5432 open
$A --step 05-8-1c check connect 192.168.0.102 192.168.0.108 22 open
# 2. Firewall on (the tool waits 20 s for it to apply)
$A --step 05-8-2 do proxmox-firewall 107 on
# 3. Outside proofs, all fresh connections
$A --step 05-8-3a check connect client 192.168.0.102 22 open
$A --step 05-8-3b check connect client 192.168.0.102 8443 blocked
$A --step 05-8-3c check connect 192.168.0.108 192.168.0.102 22 open
$A --step 05-8-3d check connect 192.168.0.108 192.168.0.102 5432 blocked
$A --step 05-8-3e check connect 192.168.0.102 192.168.0.108 22 blocked
vm 05-8-3f-ipv6 192.168.0.102 'ip -6 addr show scope global'   # must print nothing; a global IPv6 address: STOP
# 4-6. Links down, start isolated, stop the services through the Guest Agent
$A --step 05-8-4a do power 107 shutdown
$A --step 05-8-4b do link 107 down 192.168.0.102
$A --step 05-8-5 do power 107 start
$A --step 05-8-6 do quarantine-stop 107 todo-primary
# 7. Links up: restricted SSH works, everything stays stopped
$A --step 05-8-7a do link 107 up 192.168.0.102
$A --step 05-8-7b check connect 192.168.0.108 192.168.0.102 22 open
$A --step 05-8-7c check stopped 192.168.0.102
# 8. Restore normal operation: firewall off, then a reboot starts the services
$A --step 05-8-8a do proxmox-firewall 107 off
$A --step 05-8-8b do reboot 107 192.168.0.102 app
# 9. Writable, streaming over TLS with zero lag, trusted HTTPS
$A --step 05-8-9a check roles 192.168.0.102 primary
$A --step 05-8-9b check replication-tls 192.168.0.102
ops 05-8-9c-status 192.168.0.102 '--inventory initial.yaml replication-status'   # → {"changed": false}
$A --step 05-8-9d check ca 192.168.0.102
```

If streaming does not come back in 9, do not start anything or run an
installer: STOP with the logs of 8a and 8b.

#### C9.7 Phase 6 — Fence and fail over (pre-approved)

```bash
$A --step 06-1 do markers phase6
$A --step 06-2 check markers 192.168.0.108
$A --step 06-3 do fence 107
$A --step 06-4 check ports-closed 192.168.0.102 client
$A --step 06-5 check ports-closed 192.168.0.102 192.168.0.108
vm 06-6-preflight 192.168.0.108 "python3 /opt/todo/bin/app_dr.py preflight --confirm-primary-fenced 'todo-primary is fenced'"
vm 06-7-trust-ops 192.168.0.108 'cd ~/todo-operations && sha256sum --quiet -c SHA256SUMS && sudo -n sh deploy/scripts/trust-files.sh trust todo "$PWD"/deploy/dr/app_ops/*.py "$PWD"/deploy/installer/app_installer/*.py'   # → changed
vm 06-8-inventory 192.168.0.108 'cd ~/todo-operations && printf "%s\n" "user: gunstein" "hosts:" "  todo-standby: {role: current_primary, address: 192.168.0.108, local: true}" "  todo-primary: {role: rebuild_standby, address: 192.168.0.102}" > recovery.yaml && cat recovery.yaml'
$A --step 06-9 do firewall-https 192.168.0.108 192.168.0.100
ops 06-10-failover 192.168.0.108 "--inventory recovery.yaml failover --confirm-primary-fenced 'todo-primary is fenced' --confirm-promotion todo-standby" &   # → {"changed": true, "promoted_now": true, ...}; wait for exit=
vm 06-11-status 192.168.0.108 'python3 /opt/todo/bin/app_dr.py status'
$A --step 06-12 check roles 192.168.0.108 primary
$A --step 06-13 check write-probe 192.168.0.108
$A --step 06-14 check markers 192.168.0.108
$A --step 06-15 check monitor 192.168.0.108 alert   # no standby streams until the rebuild: the check must say so
```

`failover` takes several minutes, so it runs in the background like
`rebuild-standby`: wait until its log ends with `exit=`, and never start it a
second time, also not when your own tool gave up waiting (run 20 promoted in a
first attempt and hid it by running `failover` again). If it stops, its log
names the step: STOP. `"promoted_now": false` means the group was already
promoted before this step: STOP. Keep VM 107 fenced.

#### C9.8 Phase 7 — Application failover

`failover` has deployed the application. Client name resolution and CA trust
for `.108` (C9.4); a new CA is expected, the one `06-10` printed. Do not
provision the test user again.

```bash
$A --step 07-5 check services 192.168.0.108 app
$A --step 07-6 check ca 192.168.0.108
$A --step 07-7 check headers
$A --step 07-8 check browser
$A --step 07-9 do markers phase7
$A --step 07-10 check markers 192.168.0.108
ops 07-11-deploy-again 192.168.0.108 '--inventory recovery.yaml deploy-promoted-application'   # → {"changed": false}
$A --step 07-12 do reboot 108 192.168.0.108 app
$A --step 07-13 check ca 192.168.0.108
$A --step 07-14 check roles 192.168.0.108 primary
$A --step 07-15 check markers 192.168.0.108
```

#### C9.9 Phase 8 — Backup and isolated PITR (cleanup pre-approved)

```bash
ops 08-1-configure-backup 192.168.0.108 '--inventory recovery.yaml configure-backup'   # → {"changed": false}; failover configured it
$A --step 08-2 check roles 192.168.0.108 archiving
vm 08-3-backup-status 192.168.0.108 'python3 /opt/todo/bin/app_backup.py status'        # zero failed archive attempts
vm 08-4-backup-create 192.168.0.108 'python3 /opt/todo/bin/app_backup.py create'        # note the three base-... names
vm 08-5-restore-state 192.168.0.108 'podman ps -a --filter name=restore --format "{{.Names}}"; podman volume ls --filter name=restore --format "{{.Name}}"'   # must print nothing
vm 08-6-before-rows 192.168.0.108 "podman exec todo-postgres psql --username todo --dbname todo --set ON_ERROR_STOP=1 --command \"INSERT INTO todos (title, completed) VALUES ('PITR before restore point', false);\" && podman exec notes-postgres psql --username notes --dbname notes --set ON_ERROR_STOP=1 --command \"INSERT INTO notes (title) VALUES ('PITR before restore point');\""
vm 08-7-mark 192.168.0.108 'python3 /opt/todo/bin/app_backup.py mark --name acceptance_before_after'
vm 08-8-after-rows 192.168.0.108 "podman exec todo-postgres psql --username todo --dbname todo --set ON_ERROR_STOP=1 --command \"INSERT INTO todos (title, completed) VALUES ('PITR after restore point', false);\" && podman exec notes-postgres psql --username notes --dbname notes --set ON_ERROR_STOP=1 --command \"INSERT INTO notes (title) VALUES ('PITR after restore point');\""
```

The two restores use the Todo and Notes backup names that `08-4` logged:
`backup_name todo` reads the name from that log (`helpers.sh`). If a name is
missing, `app_backup.py` refuses `--backup` without a value before it changes
anything, and the step is a STOP.

```bash
vm 08-9-restore-todo 192.168.0.108 "python3 /opt/todo/bin/app_backup.py --app todo restore --backup $(backup_name todo) --target acceptance_before_after && python3 /opt/todo/bin/app_backup.py --app todo restore-status && podman inspect todo-postgres-restore --format '{{.HostConfig.NetworkMode}}' && podman exec todo-postgres-restore psql --username todo --dbname todo --command \"SELECT id, title FROM todos WHERE title LIKE 'PITR % restore point' ORDER BY id;\" && podman exec todo-postgres psql --username todo --dbname todo --command \"SELECT id, title FROM todos WHERE title LIKE 'PITR % restore point' ORDER BY id;\""
vm 08-10-restore-notes 192.168.0.108 "python3 /opt/todo/bin/app_backup.py --app notes restore --backup $(backup_name notes) --target acceptance_before_after && python3 /opt/todo/bin/app_backup.py --app notes restore-status && podman inspect notes-postgres-restore --format '{{.HostConfig.NetworkMode}}' && podman exec notes-postgres-restore psql --username notes --dbname notes --command \"SELECT id, title FROM notes WHERE title LIKE 'PITR % restore point' ORDER BY id;\" && podman exec notes-postgres psql --username notes --dbname notes --command \"SELECT id, title FROM notes WHERE title LIKE 'PITR % restore point' ORDER BY id;\""
```

Each must show `recovery|paused|read_only = t|t|on`, network `none`, only the
before-row in the restored view and both rows in the live view. Then:

```bash
vm 08-11-cleanup 192.168.0.108 'python3 /opt/todo/bin/app_backup.py --app todo cleanup-restore --confirm todo-postgres-restore && python3 /opt/todo/bin/app_backup.py --app notes cleanup-restore --confirm notes-postgres-restore && podman ps -a --filter name=restore --format "{{.Names}}" && podman volume ls --format "{{.Name}}"'   # no restore resources; the three -backup volumes remain
ops 08-12-configure-backup-again 192.168.0.108 '--inventory recovery.yaml configure-backup'   # → {"changed": false}
$A --step 08-13 do reboot 108 192.168.0.108 app
$A --step 08-14 check roles 192.168.0.108 archiving
vm 08-15-backup-status 192.168.0.108 'python3 /opt/todo/bin/app_backup.py status'   # zero failed archive attempts
$A --step 08-16 do backup-nightly 192.168.0.108   # one run of the nightly backup that failover turned on
```

#### C9.10 Phase 9 — Rebuild VM 107 as standby (pre-approved reseed)

Strictly in this order. VM 107 is stopped with every link down since phase 6.

```bash
$A --step 09-1 do proxmox-firewall 107 on
$A --step 09-2 do power 107 start
$A --step 09-3 do quarantine-stop 107 todo-primary      # warnings about failed PostgreSQL units are allowed
$A --step 09-4a do link 107 up 192.168.0.102
$A --step 09-4b check connect 192.168.0.108 192.168.0.102 22 open
$A --step 09-4c check connect 192.168.0.102 192.168.0.108 22 blocked
$A --step 09-5a check stopped 192.168.0.102
vm 09-5b-journal 192.168.0.102 'for unit in todo-postgres notes-postgres keycloak-postgres; do journalctl -b _SYSTEMD_USER_UNIT=$unit.service --no-pager | tail -n 20; done; true'
$A --step 09-6a do firewall-replication 192.168.0.108 192.168.0.102 remove
$A --step 09-6b do firewall-replication 192.168.0.102 192.168.0.108 add
$A --step 09-7 do pin-ssh 192.168.0.108 192.168.0.102
$A --step 09-8 do replication-exception 107 on
ops 09-9a-rebuild-refused 192.168.0.108 '--inventory recovery.yaml preflight-standby-rebuild --confirm-fenced todo-primary --confirm-reseed todo-primary'   # exit=1, both exact confirmations are required
ops 09-9b-preflight 192.168.0.108 "--inventory recovery.yaml preflight-standby-rebuild --confirm-fenced 'todo-primary is fenced' --confirm-reseed todo-primary"   # → {"changed": false}
ops 09-10-rebuild 192.168.0.108 "--inventory recovery.yaml rebuild-standby --confirm-fenced 'todo-primary is fenced' --confirm-reseed todo-primary" &   # → {"changed": true}; wait for exit=
```

Do not probe ports 5432-5434 before 09-10: until the rebuild publishes them,
`.108` listens there only on `127.0.0.1` and an open path looks like a
blocked one. `rebuild-standby` checks the path itself. If it fails in any way,
including `replication port ... is not reachable`: STOP, never run it again.

```bash
$A --step 09-11a check connect 192.168.0.102 192.168.0.108 5432 open
$A --step 09-11b check connect 192.168.0.102 192.168.0.108 5433 open
$A --step 09-11c check connect 192.168.0.102 192.168.0.108 5434 open
ops 09-11d-cluster-status 192.168.0.108 '--inventory recovery.yaml cluster-status'   # streaming, async, slots active, zero lag
$A --step 09-11e check roles 192.168.0.102 standby
$A --step 09-11f check replication-tls 192.168.0.108
$A --step 09-11g do markers phase9
$A --step 09-11h check markers 192.168.0.102
$A --step 09-11i check monitor 192.168.0.108 ok     # the rebuilt standby streams again
$A --step 09-11j check monitor 192.168.0.102 ok
$A --step 09-12a do proxmox-firewall 107 off
$A --step 09-12b do onboot 107 "$(recorded_onboot)"   # the value 01-3 read from the clean snapshot
```

#### C9.11 Phases 10 and 11 — Final reboots and verdict

```bash
$A --step 10-1 do reboot 107 192.168.0.102 standby    # includes: only the three PostgreSQL services
$A --step 10-2 check roles 192.168.0.102 standby
ops 10-3-cluster-status 192.168.0.108 '--inventory recovery.yaml cluster-status'
$A --step 10-4 do reboot 108 192.168.0.108 app
$A --step 10-5a check roles 192.168.0.108 archiving
$A --step 10-5b check ca 192.168.0.108
vm 10-5c-backup-status 192.168.0.108 'python3 /opt/todo/bin/app_backup.py status'
ops 10-6-cluster-status 192.168.0.108 '--inventory recovery.yaml cluster-status'
$A --step 10-7a check headers
$A --step 10-7b check markers 192.168.0.108
$A --step 10-7c check markers 192.168.0.102
$A --step 10-7d check monitor 192.168.0.108 ok     # both timers came back after the reboots
$A --step 10-7e check monitor 192.168.0.102 ok
$A --step 10-8 check disk 192.168.0.108
$A --step 11-1 check browser
$A --step 11-2 check services 192.168.0.108 app
$A --step 11-3 check services 192.168.0.102 standby
vm 11-4-restarts 192.168.0.108 'systemctl --user show -p NRestarts --value todo-app.service notes-app.service shared-proxy.service'   # 0, 0, 0
rm -f "$XDG_RUNTIME_DIR/todo-acceptance/e2e-password"
$A report full
```

`report` writes `REPORT.md` from `record.jsonl` and the logs. The verdict is
exactly one of:

- `CLEAN PASS`: `report full` says **ALL STEPS PASS** (it also checks that
  every step and log of this guide is there and nothing else), and every JSON
  line after `→` matched.
- `REPAIRED FUNCTIONAL PASS`: everything works, but something needed the
  operator (a `--operator-approved` step, a manual fix). List each with its
  original failure.
- `BLOCKED` / `IN PROGRESS`: not finished; say where and why.

Write the draft record in the style of `docs/history/ACCEPTANCE-12c3bef.md` to
`$RUN/ACCEPTANCE-<short-sha>.md`, taking every value from `REPORT.md` or a log,
never from memory. Leave both VMs in their final roles (`.108` primary with
application, `.102` database-only standby) and do not reset, promote or
rebuild anything after the verdict. Report to the operator: verdict, revision,
final topology, every deviation, and `REPORT.md`.

#### C9.13 app-ops in an agent run

Do not run `ansible` or `ansible-playbook` at all. Run every app-ops command on
the controller VM over SSH, from the package directory, and end it with the
exit code:

```bash
ssh gunstein@192.168.0.102 'cd ~/todo-operations && PYTHONPATH="$PWD/deploy/dr" PYTHONDONTWRITEBYTECODE=1 python3 -m app_ops --inventory initial.yaml replication-status; echo "exit=$?"'
```

The controller is `.102` with `initial.yaml` for phases 4-5, and `.108` with
`recovery.yaml` from phase 6. Differences from ACCEPTANCE.md in an agent run:

1. Sudo. The A3 lab sudoers file already grants passwordless sudo, so do not
   create or remove `90-app-ops-acceptance`. Skip the first refusal check
   (app-ops without NOPASSWD): you cannot take the rule away and get it back
   without a password. Record the skip; unit tests cover that refusal. Do all
   the other refusal checks.
2. Controller trust. Run the trust command with `sudo -n sh deploy/scripts/trust-files.sh ...`:
   on `.102` before C9.5, and on `.108` in C9.7.
3. Inventories. Write `initial.yaml` on `.102` before C9.5 and `recovery.yaml`
   on `.108` in C9.7, with the kickoff names and addresses, using a
   heredoc over SSH. Save both in the run folder; they hold no secrets.

The confirmations are the ones in ACCEPTANCE.md. Each repeat must print
`{"changed": false}`. Anything else is a failed gate (C3).
