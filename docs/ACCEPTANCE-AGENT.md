# Acceptance run by an autonomous agent

This document lets a coding agent run the full two-VM acceptance in
[ACCEPTANCE.md](ACCEPTANCE.md) with as little operator typing as possible.
It adds **how** the agent operates (Proxmox API token, passwordless lab sudo,
evidence, stop rules) on top of the canonical **what** in ACCEPTANCE.md.

- Part A: one-time operator preparation.
- Part B: the kickoff message the operator pastes to the agent.
- Part C: the agent's instructions. The agent follows Part C literally.

If this document and ACCEPTANCE.md disagree on a safety gate, the stricter rule
wins. If either disagrees with the App registry
(`deploy/installer/todo_installer/apps.py`) on a name, port or service, the
registry wins and the agent records the drift.

---

## Part A — Operator preparation (once, before the first agent run)

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

Shortcut: after A1 and A2, `deploy/scripts/prepare-agent-snapshots.sh` does
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
`venv`, `openssl`, `certutil` (`libnss3-tools`), OpenSSH with keys already
trusted by both VMs (`ssh gunstein@<ip> hostname` works without a password),
and internet access for the Playwright Chromium download.

Decide whether the agent may use `sudo` on the client for `/etc/hosts` and CA
trust (`CLIENT_SUDO`). If not, the agent will ask you to run exactly one
prepared command at two moments (initial deployment and failover).

### A5. Readiness check on the client

From the repository root on the client/build host, run the read-only check.
It changes nothing: local checks, Proxmox API GET requests and read-only SSH
commands. Pass the kickoff values if they differ from the lab defaults:

```bash
python3 deploy/scripts/acceptance_preflight.py --snapshot clean-agent --revision "$(git rev-parse HEAD)"
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
  CLIENT_SUDO: <yes | no>

Pre-approvals (durable for this run only; every STOP rule still applies):
  [x] Reset both VMs to the clean snapshots above (destroys their current state)
  [x] Guest Agent guest-exec opt-in and SELinux helper entrypoint opt-in on VM 107
  [x] Quarantine rehearsal with a brief outage of VM 107 before promotion
  [x] Fence VM 107 and promote the standby database group
  [x] Destructive reseed of VM 107's three databases after verified backup/PITR
  [x] Cleanup of the disposable PITR restore containers/volumes
  [x] Store the generated testuser password in a 0600 tmpfs file for this run
Not approved: anything else destructive, any source change, commit or push.
```

---

## Part C — Agent instructions

### C1. Your job, in one paragraph

You execute the eleven phases of `docs/ACCEPTANCE.md` on two Proxmox lab VMs,
in order, on one clean Git revision, and you collect evidence for every phase.
You work alone: you use SSH to the VMs, the Proxmox API through
`deploy/scripts/pve_lab.py`, and passwordless sudo inside the VMs. You ask the
operator only in the cases listed in C4. You never change source code. At the
end you write a verdict. Being careful is more important than being fast.

### C2. Absolute rules (never break these)

1. **Never** edit, commit or push files in the Git checkout. A source change
   makes the run REPAIRED, not CLEAN. If something in the repository is wrong,
   STOP and report it.
2. **Never** print, log or store a secret except where C6 says so. Secrets are:
   the Proxmox token, the Keycloak admin password, the testuser password and any
   `podman secret` value. Never run `set -x`. Never `cat` `pve.env`.
3. **Never** retry a destructive or one-shot command after it failed or timed
   out: `todo_dr.py promote`, `rebuild-standby.yml`, snapshot rollback, volume
   deletion, `todo_backup.py restore --replace`, `cleanup-restore`. Inspect state
   instead (C8).
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

- A STOP condition (C3).
- `pve_lab.py` returns HTTP 401/403, or either the datacenter firewall
  (`get /cluster/firewall/options`) or the node's own firewall
  (`get /nodes/{node}/firewall/options`) shows `enable: 0` or no `enable`: VM
  firewall rules would have no effect either way. Ask; do not enable either
  yourself (A1 should have enabled the node firewall ahead of time).
- `CLIENT_SUDO: no` and client `/etc/hosts` or CA trust must change (phase 3
  and phase 7). Give the operator exactly the two commands in C9.4 and wait for
  "done". Then verify the result yourself.
- Anything the kickoff message did not pre-approve.

Otherwise do not ask. Report progress briefly at the end of each phase.

### C5. Tools you use

**Run folder.** Create `~/todo-acceptance-runs/<RUN_ID>/` (outside the checkout)
with `logs/`. Copy the run-record template from `docs/ACCEPTANCE.md`
("Run record / handoff template") into `run-record.md` there. Save the output
of every command that matters to `logs/<phase>-<step>.log`, for example:

```bash
ssh gunstein@192.168.0.102 'systemctl --user is-active todo-app.service' 2>&1 | tee ~/todo-acceptance-runs/$RUN_ID/logs/03-services.log
```

**Proxmox API.** Always use the helper from the checkout; never write your own
curl commands with the token:

```bash
python3 deploy/scripts/pve_lab.py get /nodes/{node}/qemu/107/status/current
python3 deploy/scripts/pve_lab.py get /nodes/{node}/qemu/107/config
python3 deploy/scripts/pve_lab.py task /nodes/{node}/qemu/107/snapshot/clean-agent/rollback
python3 deploy/scripts/pve_lab.py task /nodes/{node}/qemu/107/status/start
python3 deploy/scripts/pve_lab.py task /nodes/{node}/qemu/107/status/shutdown
python3 deploy/scripts/pve_lab.py task /nodes/{node}/qemu/107/status/stop
python3 deploy/scripts/pve_lab.py task /nodes/{node}/qemu/107/status/reboot
python3 deploy/scripts/pve_lab.py post /nodes/{node}/qemu/107/agent/ping
python3 deploy/scripts/pve_lab.py exec 107 -- /opt/todo/bin/todo-quarantine.sh check todo-primary gunstein
python3 deploy/scripts/pve_lab.py nic 107 link_down 1
python3 deploy/scripts/pve_lab.py set /nodes/{node}/qemu/107/config onboot=0
python3 deploy/scripts/pve_lab.py get /nodes/{node}/qemu/107/firewall/options
python3 deploy/scripts/pve_lab.py get /nodes/{node}/qemu/107/firewall/rules
python3 deploy/scripts/pve_lab.py get /cluster/firewall/options
python3 deploy/scripts/pve_lab.py get /cluster/ha/resources
```

`exec` prints `exited`, `exitcode`, `out-data` and `err-data` and exits with the
guest command's exit code. Exit code `124` means the guest command did not
finish: do not repeat it; poll
`get "/nodes/{node}/qemu/107/agent/exec-status?pid=<PID>"` instead.
`task` waits and fails unless the Proxmox task ends with `OK`.
`nic` changes one flag on every network device and keeps MAC and bridge.

**Guests.** SSH as `gunstein` with host-key checking on. Use `sudo -n` for root
commands inside the VMs so a missing sudo rule fails immediately instead of
hanging. Run Ansible **without** `--ask-become-pass`; passwordless sudo
replaces it. Everything else in ACCEPTANCE.md stays the same.

**Long commands.** Bundle builds, `bootstrap-standby.yml` and
`rebuild-standby.yml` can take more than ten minutes. Start them in the
background with output to a log file, for example
`nohup ssh ... 'cd ~/todo-operations && ansible-playbook ...' > logs/09-rebuild.log 2>&1 &`,
then read the log until `PLAY RECAP` appears. If your tool times out, the
command may still be running: read the log and `ps`; never start it a second time.

**Interactive prompts in ACCEPTANCE.md.** Replace `read -rp "Client IPv4..."`
style prompts for non-secret values with the value from the kickoff. For
secrets, use C6 instead of `read -rsp`.

**Waiting for the Guest Agent.** After a VM start, `post .../agent/ping`
fails until the agent is up. Repeat it every 10 seconds for up to 5 minutes;
that repetition is read-only and allowed.

**Waiting for a reboot.** Record the boot ID first
(`cat /proc/sys/kernel/random/boot_id`), reboot through the API, then poll SSH
every 10 seconds for up to 10 minutes until the boot ID differs.

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

For every phase, write into `run-record.md`:

- Ansible `PLAY RECAP` lines (ok/changed/failed counts) and for repeats `changed=0`.
- `hostname`, boot ID before and after each reboot.
- Per database (todo, notes, keycloak): role (`pg_is_in_recovery`,
  `transaction_read_only`), receive/replay LSNs, lag, slot name and state.
- Marker titles and IDs (Todo and Notes) and on which host you read them.
- CA SHA-256 fingerprint of the serving host.
- Browser test summary lines (`N passed`, and **0 skipped**).
- Proxmox evidence: VM status, `onboot`, every `netN` string, VM firewall
  options and the rules you created (with comments).
- Every deviation from ACCEPTANCE.md and why.

Environment deviations that are expected in an agent run and must be recorded,
but do not by themselves downgrade the verdict: passwordless sudo instead of
`--ask-become-pass`; Proxmox API token instead of the node Shell; the testuser
password in a tmpfs file; firewall evidence from API rule listings plus
connection tests instead of `pve-firewall` output.

### C8. If a command fails or times out

1. Do not run it again.
2. Save the complete output.
3. Check the current state read-only (service status, database role, Proxmox
   task list `get /nodes/{node}/tasks?vmid=107`, `podman ps -a`).
4. Only read-only commands, waits for asynchronous work (fapolicyd refresh,
   replication catching up, Keycloak starting after boot) and re-running a
   read-only check may be repeated. Everything else: STOP (C3).

### C9. Phase-by-phase instructions

Execute the phases of `docs/ACCEPTANCE.md` in order. Read each phase completely
before starting it. The notes below tell you how to perform the steps that
normally need the operator, and what extra checks are required. Values below
use the lab defaults (`.102` = VM 107 = `todo-primary`, `.108` = VM 108 =
`todo-standby`, client `.100`); use the kickoff values.

#### C9.0 Before phase 1

1. Read `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/ACCEPTANCE.md`,
   `docs/PROXMOX-QUARANTINE.md` and `docs/ACCEPTANCE-TROUBLESHOOTING.md`.
2. `git status --porcelain` must be empty and `git rev-parse HEAD` must equal the
   kickoff revision. Otherwise STOP.
3. If CI is not `green`, run the local suite and record the result; STOP on failure:

   ```bash
   python3 -m venv ~/todo-acceptance-runs/$RUN_ID/venv
   ~/todo-acceptance-runs/$RUN_ID/venv/bin/pip install -r deploy/ansible/requirements.txt jinja2 PyYAML || \
     ~/todo-acceptance-runs/$RUN_ID/venv/bin/pip install ansible-core==2.14.18 jinja2 PyYAML
   PATH=~/todo-acceptance-runs/$RUN_ID/venv/bin:$PATH python -m unittest discover --start-directory tests
   ```

   (`ansible-core` 2.20 needs Python 3.12 or newer; the fallback is the Oracle
   Linux baseline version, which CI also tests.)

4. Print the registry and compare it with the table in ACCEPTANCE.md
   ("Registered workload group"); record any drift:

   ```bash
   PYTHONPATH=deploy/installer python3 -m todo_installer replication-apps --details
   ```

5. Check the Proxmox helper and access: `get /version`, both VMs'
   `status/current` and `config`, `get /cluster/firewall/options`,
   `get /cluster/ha/resources`. Record `onboot` and every `netN` value for both
   VMs; you restore `onboot` at the end.

#### C9.1 Phase 1 — Clean-host evidence (pre-approved reset)

1. Roll back both VMs to their clean snapshots with `task .../snapshot/<SNAP>/rollback`,
   then `task .../status/start` if the VM is stopped. Wait for `agent/ping` and SSH.
2. VM firewall must be off on both: `get .../firewall/options` shows no
   `enable: 1`. If a VM firewall is enabled, read all its rules; if every rule
   has a `todo-quarantine` comment from an earlier run, set `enable=0` with
   `set /nodes/{node}/qemu/<VMID>/firewall/options enable=0` and record why.
   Any other rule: STOP.
3. Every `netN` must not contain `link_down=1`. If it does, `nic <VMID> link_down 0` and record.
4. Run the phase 1 guest checks from ACCEPTANCE.md on both VMs over SSH, plus
   `sudo -n true && echo SUDO-OK` and `nmcli -g IP4.ADDRESS,IP4.GATEWAY device show`
   (record whether the address comes from DHCP). Save to logs.
5. PASS only if identities differ and match the kickoff, security services are
   active, SELinux is Enforcing, Podman is rootless, `python3 -c 'import jinja2, yaml'`
   succeeds on both VMs, and there is no Todo, Notes or Keycloak state. If Jinja2
   or PyYAML is missing, `sudo -n dnf install -y python3-jinja2 python3-pyyaml`
   is a documented target prerequisite (`deploy/offline/README.md`), not a source
   change; install it and record the deviation. If Todo/Notes/Keycloak state
   exists after rollback: STOP (wrong snapshot).

#### C9.2 Phase 2 — Build and stage

Follow ACCEPTANCE.md phase 2 exactly on the client. Transfer both archives and
their `.sha256` files with `scp` to both VMs, then verify there. Record both
`VERSION` files; both must show the kickoff revision and `source_state=clean`.

#### C9.3 Phase 3 — Initial deployment on `.102`

1. Apply the fapolicyd exact-file trust recipe from `deploy/offline/README.md`
   (with `sudo -n`), then `sh ./preflight.sh` and
   `sh ./install.sh --publish-address 192.168.0.102` over SSH.
2. Add the firewalld HTTPS rule for the client IP (value from the kickoff).
3. Client name resolution and CA trust: see C9.4.
4. Run the seven-service, nginx and health checks of phase 3.
5. Browser environment on the client (once per run):

   ```bash
   python3 -m venv todo-backend/.venv
   todo-backend/.venv/bin/python -m pip install -r todo-backend/requirements-e2e.txt
   todo-backend/.venv/bin/python -m playwright install chromium
   ```

   `todo-backend/.venv` is ignored by Git; confirm `git status --porcelain` is
   still empty afterwards.
6. Provision `testuser` (C6 password). `e2e/provision_user.py` talks to
   `http://127.0.0.1:8080`, so forward that port from the serving host:

   ```bash
   ssh -o ExitOnForwardFailure=yes -f -N -L 127.0.0.1:8080:127.0.0.1:8080 gunstein@192.168.0.102
   (
     KEYCLOAK_ADMIN_PASSWORD="$(ssh gunstein@192.168.0.102 "podman secret inspect --showsecret --format '{{.SecretData}}' keycloak-admin-password")"
     E2E_PASSWORD="$(cat "$XDG_RUNTIME_DIR/todo-acceptance/e2e-password")"
     export KEYCLOAK_ADMIN_PASSWORD E2E_PASSWORD
     todo-backend/.venv/bin/python e2e/provision_user.py
   )
   pkill -f 'ssh -o ExitOnForwardFailure=yes -f -N -L 127.0.0.1:8080:127.0.0.1:8080'
   ```

   Provision once. The user lives in the replicated Keycloak database and must
   survive failover; do not re-provision after promotion unless login fails
   (then STOP first).
7. Browser tests: run the block from ACCEPTANCE.md, but set the password from
   the file instead of `read -rsp`:

   ```bash
   (
     E2E_PASSWORD="$(cat "$XDG_RUNTIME_DIR/todo-acceptance/e2e-password")"
     export E2E_PASSWORD E2E_USERNAME=testuser
     E2E_BASE_URL=https://todo.test:8443 E2E_IGNORE_HTTPS_ERRORS=false \
       todo-backend/.venv/bin/python -m pytest e2e/test_todo_flow.py --browser chromium -q
     E2E_MULTI_APP=1 E2E_CA_FILE=/tmp/todo-public-root.crt E2E_IGNORE_HTTPS_ERRORS=false \
       todo-backend/.venv/bin/python -m pytest e2e/test_multi_app.py -q
   )
   ```

   Require all passed and **0 skipped**. A skip is a failure.
8. Persistent markers: create one authenticated Todo and one Note with the
   helper and record the printed IDs:

   ```bash
   (
     E2E_PASSWORD="$(cat "$XDG_RUNTIME_DIR/todo-acceptance/e2e-password")"
     export E2E_PASSWORD MARKER="acceptance $RUN_ID phase3"
     todo-backend/.venv/bin/python e2e/create_markers.py
   )
   ```

   Use the same pattern later with `phase4`, `phase6`, `phase7` and `phase9`
   in the marker text.
9. Reboot VM 107 through the API (C5), repeat the checks, the markers and the
   CA fingerprint, rerun `install.sh` and compare before/after values as
   ACCEPTANCE.md requires.

#### C9.4 Client name resolution and CA trust (phase 3 and phase 7)

With `CLIENT_SUDO: yes`, run these yourself. With `CLIENT_SUDO: no`, show the
operator exactly these commands, with the first line set to the current serving
host (`.102` in phase 3, `.108` in phase 7), and wait:

```bash
IP=192.168.0.102
sudo sed -i -e '/[[:space:]]todo\.test\([[:space:]]\|$\)/d' -e '/[[:space:]]notes\.test\([[:space:]]\|$\)/d' /etc/hosts && echo "$IP todo.test notes.test" | sudo tee -a /etc/hosts
deploy/scripts/trust-serving-ca.sh "gunstein@$IP"
```

Afterwards verify yourself: `getent hosts todo.test notes.test`, the fingerprint
comparison from ACCEPTANCE.md, and `curl --fail https://todo.test:8443/ready`
and `https://notes.test:8443/ready` **without** `-k`. Also save the public CA to
`/tmp/todo-public-root.crt` for `E2E_CA_FILE`.

#### C9.5 Phase 4 — Standby bootstrap

Follow ACCEPTANCE.md. The firewalld rule on `.102` uses `port="5432-5434"`.
Set up primary-to-standby SSH with C9.12 (`FROM=.102`, `TO=.108`); do not use
`ssh-copy-id`, which would need a password. Record per-database
streaming/slot/lag evidence and read both markers on the standby. Reboot VM 108
through the API and re-check.

#### C9.6 Phase 5 — DR tool and quarantine rehearsal (pre-approved outage)

1. `install-dr-tool.yml`, then `todo_dr.py status` on `.108`, then the repeat
   (`changed=0`).
2. On `.102`, install the quarantine helper with both pre-approved opt-ins:

   ```bash
   ansible-playbook --inventory deploy/ansible/inventories/initial/hosts.ini \
     deploy/ansible/playbooks/install-quarantine-tool.yml \
     -e todo_quarantine_enable_guest_exec=true -e todo_quarantine_enable_selinux_entrypoint=true
   ```

3. `pve_lab.py exec 107 -- /opt/todo/bin/todo-quarantine.sh check todo-primary gunstein`
   must exit 0 and print `READY`.
4. Prepare the quarantine profile on VM 107 **while it is still disabled**:
   - `get /cluster/firewall/options`: datacenter firewall must be enabled (else C4).
   - `get /nodes/{node}/firewall/options`: the node's own firewall must also be
     enabled (else C4; this is separate from the datacenter switch above and from
     VM 107's own `enable` flag, and A1 should have turned it on ahead of time).
   - `get /nodes/{node}/qemu/107/firewall/rules`: if any rule remains from an
     incomplete earlier run (its Proxmox firewall config is not part of the VM
     snapshot, so phase 1's rollback does not clear it), and every one of them
     has a `todo-quarantine-*` comment, delete each by its `pos` with
     `delete /nodes/{node}/qemu/107/firewall/rules/<pos>` (highest `pos` first)
     and record why. Any rule without that comment prefix: STOP.
   - `nic 107 firewall 1` needs `SDN.Use` on the token (A1); a `403` naming
     `/sdn/zones/.../SDN.Use` means A1 was not completed — ask the operator (C4).
   - Every `netN` of VM 107 needs `firewall=1`: `nic 107 firewall 1`.
   - Create exactly these rules, each with its comment:

     ```bash
     python3 deploy/scripts/pve_lab.py post /nodes/{node}/qemu/107/firewall/rules type=in action=ACCEPT proto=tcp dport=22 source=192.168.0.100/32 enable=1 comment=todo-quarantine-ssh-client
     python3 deploy/scripts/pve_lab.py post /nodes/{node}/qemu/107/firewall/rules type=in action=ACCEPT proto=tcp dport=22 source=192.168.0.108/32 enable=1 comment=todo-quarantine-ssh-peer
     python3 deploy/scripts/pve_lab.py post /nodes/{node}/qemu/107/firewall/rules type=out action=ACCEPT proto=tcp dport=5432:5434 dest=192.168.0.108/32 enable=0 comment=todo-quarantine-replication
     ```

   - Set the options but keep the firewall off. `dhcp=1` and `ndp=1` keep
     DHCP and IPv6 neighbour discovery working, so the guest keeps its address
     under quarantine:
     `set /nodes/{node}/qemu/107/firewall/options enable=0 policy_in=DROP policy_out=DROP dhcp=1 ndp=1`.
   - Record `get .../firewall/rules` and `get .../firewall/options`.
5. Rehearsal (VM 107 is still the writable primary, so a brief exposure is
   harmless). A blocked port only proves something when a service is really
   listening behind it, so the outside proofs run **before** services stop:
   1. Baseline with the firewall still off: from the client
      `curl --fail https://todo.test:8443/ready` works; from `.108`
      `timeout 5 bash -c '</dev/tcp/192.168.0.102/5432'` works; from `.102`
      `timeout 5 bash -c '</dev/tcp/192.168.0.108/22'` works. If any fails, STOP.
   2. `set .../107/firewall/options enable=1` (VM running, links up).
   3. Outside proofs, all fresh connections:
      - Client to `.102`: `ssh gunstein@192.168.0.102 hostname` works;
        `curl --connect-timeout 5 https://todo.test:8443/ready` fails.
      - `.108` to `.102`: `ssh gunstein@192.168.0.102 hostname` works;
        `timeout 5 bash -c '</dev/tcp/192.168.0.102/5432'` fails.
      - `.102` to `.108`: `timeout 5 bash -c '</dev/tcp/192.168.0.108/22'` fails
        (outbound is blocked; the replication exception is still disabled).
      - If `.102` has a global IPv6 address (`ip -6 addr show scope global`),
        repeat one inbound and one outbound test over IPv6; both must fail.
      Any unexpected result: STOP (you may set `enable=0` first; `.102` is still
      the authorized primary in this phase).
   4. `task .../107/status/shutdown`; then `nic 107 link_down 1`.
   5. `task .../107/status/start` (firewall stays enabled); wait for `agent/ping`.
   6. `exec 107 -- /opt/todo/bin/todo-quarantine.sh stop todo-primary gunstein`:
      require exit 0, `exited: 1` and `STOPPED` in `out-data`.
   7. `nic 107 link_down 0`. Wait up to three minutes for the guest to get
      its address back. SSH from the client and from `.108` must work.
      On `.102`: every service from `apps.services()` is inactive or failed
      with zero MainPID and ControlPID, and `podman ps` shows no running containers.
   8. Restore normal operation: `set .../107/firewall/options enable=0`, then
      `task .../107/status/reboot` so the services start at boot.
   9. Require all seven services active on `.102`, `.102` writable for all three
      databases, streaming with zero lag to `.108` again, and trusted HTTPS from
      the client. Only then continue.

#### C9.7 Phase 6 — Fence and promote (pre-approved)

1. Create the `phase6` markers and read them on `.108`.
2. Fence VM 107:
   `task .../107/status/stop`, `set .../107/config onboot=0`, `nic 107 link_down 1`.
   `get /cluster/ha/resources` must not list `vm:107`.
   Record `status/current` (`stopped`) and every `netN` (`link_down=1`).
3. From `.108` and from the client: `.102` ports 22, 5432, 5433, 5434 and 8443
   must be unreachable (`timeout 5 bash -c '</dev/tcp/...'` fails).
4. On `.108`: `todo_dr.py preflight`, `promote`, `status` exactly as in
   ACCEPTANCE.md (the confirmation strings are fixed). Run `promote` once.
5. Rolled-back write probes on Todo and Notes, markers present, all three
   databases `f|off`.

#### C9.8 Phase 7 — Application failover

Follow ACCEPTANCE.md on `.108`. Then C9.4 with `<IP>` = `.108` (a new CA is
expected). Do not re-provision `testuser`. Run the browser tests and create
`phase7` markers. Repeat run `changed=0`, reboot VM 108 through the API,
re-check.

#### C9.9 Phase 8 — Backup and isolated PITR (cleanup pre-approved)

Follow ACCEPTANCE.md exactly, including `--app todo` and `--app notes` for
restore commands. Record every backup name printed by `create`
(`todo: ...`, `notes: ...`, `keycloak: ...`). Existing restore state before
`restore` is a STOP. After both comparisons pass, run the two
`cleanup-restore` commands and verify only the restore resources vanished.

#### C9.10 Phase 9 — Rebuild VM 107 as standby (pre-approved reseed)

1. VM 107 is stopped with links down. `set .../107/firewall/options enable=1`
   (the replication rule is still `enable=0`).
2. `task .../107/status/start`; wait for `agent/ping`.
3. `exec 107 -- /opt/todo/bin/todo-quarantine.sh stop todo-primary gunstein`:
   exit 0 and `STOPPED`. A warning about failed units is allowed; save it.
4. `nic 107 link_down 0`. SSH from the client and from `.108` must work; from
   `.102`, `timeout 5 bash -c '</dev/tcp/192.168.0.108/22'` must fail. Record
   `get .../107/firewall/options` and `.../rules`; they must equal the profile
   from the rehearsal (replication rule still `enable=0`).
5. On `.102` via SSH: services stopped, no running containers; read
   `journalctl -b _SYSTEMD_USER_UNIT=todo-postgres.service` (and the notes and
   keycloak PostgreSQL units) into logs.
6. On `.102`: remove the old inbound replication rich rule (5432-5434 from
   `.108`) with `sudo -n firewall-cmd --permanent --remove-rich-rule=...` and
   reload. On `.108`: add the rule allowing only `.102` to `.108` ports
   5432-5434, and reload.
7. Key-based SSH from `.108` to `.102` with C9.12 (`FROM=.108`, `TO=.102`).
8. Enable only the replication exception: read `get .../107/firewall/rules`,
   find the rule whose comment is `todo-quarantine-replication`, read its `pos`,
   then `set /nodes/{node}/qemu/107/firewall/rules/<pos> enable=1`. Verify from
   `.102`: `timeout 5 bash -c '</dev/tcp/192.168.0.108/5432'` (and 5433, 5434)
   now succeed.
9. On `.108`, the recovery inventory as in phase 7, then the read-only
   `preflight-standby-rebuild.yml` **without** `--ask-become-pass`. Every
   assertion must pass.
10. `rebuild-standby.yml` once, **without** `--ask-become-pass`, in the
    background with a log (C5). Do not start it twice. Wait for `PLAY RECAP`.
    Any `failed=` other than 0: STOP; never rerun.
11. `cluster-status.yml`: every database reports streaming, async, active slot,
    zero lag, and `.102` read-only. Create `phase9` markers and read them on `.102`.
12. After phase 9 passes, lift the quarantine as the 12c3bef run did:
    `set .../107/firewall/options enable=0`, and restore VM 107 `onboot` to the
    value recorded in C9.0. Record both.

#### C9.11 Phase 10 and 11 — Final reboots and verdict

1. Reboot VM 107 only, verify, run `cluster-status.yml`. Then reboot VM 108
   only, verify, run `cluster-status.yml` again. Never both at once.
2. Final browser tests from the client (0 skipped), all markers, CA fingerprint,
   `NRestarts=0` for `todo-app.service`, `notes-app.service`,
   `shared-proxy.service`, no failed user units on either VM.
3. Verdict, exactly one of:
   - `CLEAN PASS`: every phase passed on the kickoff revision, no source change,
     full evidence (C7).
   - `REPAIRED FUNCTIONAL PASS`: everything works, but something had to be
     repaired during the run. List each repair and its original failure.
   - `BLOCKED` / `IN PROGRESS`: not finished; say where and why.
4. Write a draft evidence record in the style of `docs/history/ACCEPTANCE-12c3bef.md` to
   `~/todo-acceptance-runs/<RUN_ID>/ACCEPTANCE-<short-sha>.md`. Do not copy it
   into the repository; the operator decides.
5. Delete `$XDG_RUNTIME_DIR/todo-acceptance/e2e-password`. Leave both VMs in the
   final roles (`.108` primary with application, `.102` database-only standby).
   Do not reset, promote or rebuild anything after the verdict.
6. Report to the operator: verdict, revision, final topology, and every deviation.

#### C9.12 Key-based SSH between the VMs (no password needed)

Ansible on `FROM` must reach `TO` with a key, and `FROM` must know `TO`'s host
key through an independently verified fingerprint. Your own SSH connections
from the client are the trusted path. Run from the client (example
`FROM=192.168.0.102`, `TO=192.168.0.108`):

```bash
FROM=192.168.0.102 TO=192.168.0.108
ssh gunstein@$FROM "test -f ~/.ssh/id_rsa || ssh-keygen -q -t rsa -b 3072 -N '' -C todo-ansible-control -f ~/.ssh/id_rsa"
ssh gunstein@$FROM 'cat ~/.ssh/id_rsa.pub' |
  ssh gunstein@$TO 'umask 077; mkdir -p ~/.ssh; read -r key; grep -qxF "$key" ~/.ssh/authorized_keys 2>/dev/null || printf "%s\n" "$key" >> ~/.ssh/authorized_keys'
FP=$(ssh gunstein@$TO 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub' | awk '{print $2}')
ssh gunstein@$FROM bash -s -- "$TO" "$FP" <<'PIN'
set -eu
host=$1 expected=$2
scanned=$(ssh-keyscan -t ed25519 "$host" 2>/dev/null)
actual=$(printf '%s\n' "$scanned" | ssh-keygen -lf - | awk '{print $2}')
test "$actual" = "$expected" || { echo "FINGERPRINT MISMATCH: $actual != $expected" >&2; exit 1; }
ssh-keygen -F "$host" >/dev/null || printf '%s\n' "$scanned" >> ~/.ssh/known_hosts
echo "PINNED $host $actual"
PIN
ssh gunstein@$FROM "ssh -o BatchMode=yes gunstein@$TO hostname"
```

A fingerprint mismatch is a STOP. The last command must print `TO`'s hostname.
