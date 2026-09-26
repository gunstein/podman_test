# Acceptance

This is the canonical normal execution sequence for full two-VM acceptance of
the seven-pod, two-application Podman Kube architecture: Todo and Notes, shared
Keycloak and shared nginx, with three independently replicated PostgreSQL
databases (Todo, Notes and Keycloak). Use the direct DR tools and the
plain-SSH `app-ops` commands below ([app-ops](../deploy/ops/README.md)). The final rebuild permanently replaces old-primary database
data; use disposable lab hosts and explicit infrastructure fencing.

[688a0f6](history/ACCEPTANCE-688a0f6.md), [12c3bef](history/ACCEPTANCE-12c3bef.md),
[9e54cfb](history/ACCEPTANCE-9e54cfb.md), [3fb897f](history/ACCEPTANCE-3fb897f.md), [f1f07b5](history/ACCEPTANCE-f1f07b5.md), [1b1d345](history/ACCEPTANCE-1b1d345.md), [0604c56](history/ACCEPTANCE-0604c56.md) and [2165933](history/ACCEPTANCE-2165933.md) are historical evidence, not current machine
state or authorization. A NEW run evaluates its own clean revision. Use
[troubleshooting](ACCEPTANCE-TROUBLESHOOTING.md) only when a gate fails;
[Proxmox quarantine](PROXMOX-QUARANTINE.md) supplies the specialized
infrastructure procedure. An autonomous agent run additionally follows
[ACCEPTANCE-AGENT.md](ACCEPTANCE-AGENT.md) for Proxmox API, sudo, secret and
evidence handling. The pages under [deploy/ops](../deploy/ops/README.md#the-workflows)
describe each operation's contract, not another acceptance sequence. Ansible is
retired; runs before `f1f07b5` used its playbooks.

## Entry, approvals and evidence

1. Choose the entry path. **NEW:** use the current architecture, this runbook
   and the selected revision; start from the documented clean
   baseline after reset approval. Never infer current state from an earlier run.
   **CONTINUATION:** read the private run record and obtain fresh role, fencing
   and database status before acting. Never reset or repeat promotion/rebuild just
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
| Client/build terminal (ThinkPad in this lab) | Build, transfer, run guest commands through SSH, configure client DNS/CA and execute browser tests. An agent with access can do scoped checks here. |
| Proxmox **node Shell** | Operator pastes reviewed `qm`/`pvesh` commands. No SSH to the hypervisor and no typing in the guest console are required by this procedure. |
| Guest, reached through SSH | app-ops and rootless Podman run as the service user. app-ops uses `sudo -n` only (see [before phase 4](#before-phase-4-trust-inventories-and-sudo)). Never send sudo passwords to an agent. |

Paste only the command block, not prompts such as `root@proxmox:~#`.
After each operator action, inspect its result before giving the next mutation.
Batch independent read-only checks; do not batch across a fencing or deletion
gate. Agents should run available checks themselves instead of asking the
operator to copy logs repeatedly. Report the next location explicitly. If most
steps end up relaying commands through the operator's terminal or the Proxmox
node Shell, consider asking the operator about scoped Proxmox API or sudo
access before starting the next run; that is the largest lever for reducing
round trips, but it is the operator's access decision to make, not the
agent's to assume.

### Run record / handoff template

Copy this into a private run log (no passwords, tokens or secret payloads):

```text
Run ID / operator / date:
Mode: NEW clean run | CONTINUATION
Git revision / both VERSION values / archive checksums:
Topology: initial primary hostname/IP/VMID/NIC; initial standby equivalents;
          client source IP; Proxmox node; actual clean snapshot names:
Current roles and fencing: which DB is writable, VM power/link/firewall state:
Last completed phase / exact command / result and evidence:
Next phase / where to run it / approval still required:
Markers: original Todo and Notes ID/title; final authenticated Todo and Notes ID/title:
Backup name per database / restore-point name / isolated comparisons / cleanup:
Boot IDs before/after / TLS CA fingerprint / browser tests (no skips):
Operations tool: app-ops (deploy/ops)
Deviations and repairs (keep original failure evidence):
Verdict: IN PROGRESS | BLOCKED | REPAIRED FUNCTIONAL PASS | CLEAN PASS
```

Before a NEW run, verify the selected clean commit and CI result, and check
client/build, SSH and Chromium tools. Read AGENTS.md and ARCHITECTURE.md.
Start with observations and a plan; obtain separate explicit approvals for reset,
Guest Agent security opt-ins, fencing/promotion, destructive reseed and disposable
restore cleanup. No source edits, commits, pushes or automatic reset follow a
verdict. CONTINUATION uses the existing run record plus fresh observations.
For a previously failed operation, obtain fresh database role and command
evidence before acting; never blindly retry a refused destructive stage.

## Tested topology

| Role at start | Hostname | Address |
|---|---|---|
| Initial primary | `todo-primary` | `192.168.0.102` |
| Initial standby | `todo-standby` | `192.168.0.108` |
| Client/build host | operator laptop | `192.168.0.100` |

After promotion and rebuild, machine names stay fixed while roles reverse:

| Current role | Hostname | Address |
|---|---|---|
| Primary, application and backup | `todo-standby` | `192.168.0.108` |
| Database-only standby | `todo-primary` | `192.168.0.102` |

Both runtimes use Oracle Linux 9.8, SELinux enforcing, active `fapolicyd` and
firewalld, the system Python 3.9 with Jinja2 and PyYAML, user lingering, 4 GiB memory and
an 18 GiB home filesystem per VM.

These values describe the lab used for the earlier four-pod runs. The
seven-pod topology runs three PostgreSQL instances plus Keycloak per host; record
free memory (`free -m`) in phase 1 and STOP if the hosts swap heavily after
phase 3.

The tested Kube baseline is rootless Podman 5.8.2. The installer verifies the required
`podman kube play --no-pod-prefix` capability to preserve operational container
names; this does not claim a minimum supported Podman version.

## Registered workload group

The App registry in `deploy/installer/app_installer/apps.py` is the source of
truth for names below. If this table and the registry disagree, the registry
wins; record the drift as a documentation defect. Print the current group on
the build host with:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer replication-apps --details
PYTHONPATH=deploy/installer python3 -c 'from app_installer import apps; print(" ".join(apps.services()))'
```

| Pod / user service | Contents | Runs on |
|---|---|---|
| `shared-proxy` / `shared-proxy.service` | `nginx` (TLS, routing by hostname) | Serving host |
| `todo-app` / `todo-app.service` | `todo-migrate` init, `todo-backend`, `todo-frontend` | Serving host |
| `notes-app` / `notes-app.service` | `notes-migrate` init, `notes-backend`, `notes-frontend` | Serving host |
| `keycloak` / `keycloak.service` | `keycloak` | Serving host |
| `todo-postgres` / `todo-postgres.service` | `todo-postgres` | Both hosts |
| `notes-postgres` / `notes-postgres.service` | `notes-postgres` | Both hosts |
| `keycloak-postgres` / `keycloak-postgres.service` | `keycloak-postgres` | Both hosts |

| Database | Container | psql user | Host replication port | Standby slot | Rebuilt slot | Marker table |
|---|---|---|---|---|---|---|
| todo | `todo-postgres` | `todo` | 5432 | `todo_standby` | `todo_rebuilt_standby` | `todos (title, completed)` |
| notes | `notes-postgres` | `notes` | 5433 | `notes_standby` | `notes_rebuilt_standby` | `notes (title, body)` |
| keycloak | `keycloak-postgres` | `keycloak` | 5434 | `keycloak_standby` | `keycloak_rebuilt_standby` | none; verify via login |

All three databases are one DR group: bootstrap, promotion, backup, rebuild and
status always act on the complete group, never on one database alone. A
serving host runs all seven services; a rebuilt standby runs only the three
PostgreSQL services. Clients use `https://todo.test:8443` and
`https://notes.test:8443`; both names map to the serving host and are covered
by one SAN certificate. The shared issuer is
`https://todo.test:8443/auth/realms/todo`.

## Acceptance rules

- Start from clean, independently identifiable hosts.
- Build both packages from one clean Git revision.
- Verify checksums before extraction and compare `VERSION` on both hosts.
- Never place secret values in a file, transcript or Git.
- Keep old primary fenced from promotion until its old services are stopped and
  its data is deliberately re-seeded.
- Never skip a read-only preflight.
- Do not rerun the initial installer after replication has been configured.
- Never rerun a one-shot destructive workflow blindly after partial failure.
- Never reboot both final database nodes at the same time.

## Where to change VM addresses

Choose addresses before starting a clean drill. IPs printed here are lab
examples. Keep the service name `todo.test`: moving it to another IP does not
require changing the realm, frontend, certificate hostname or manifest templates.

| Setting | Where to change it | What to enter |
|---|---|---|
| VM network | Guest OS or DHCP reservation | Fixed address per VM; verify with `ip -brief -4 address` |
| Initial HTTPS binding | Primary: `sh ./install.sh --publish-address PRIMARY_IP` | Primary's own IPv4, on every install/rerun |
| Replication and SSH | Primary's `todo-operations/initial.yaml` | Each host's own IPv4 (`address`), matching `ip -4 address` on that host |
| Recovery/rebuild | Promoted host's `todo-operations/recovery.yaml` | Same machine IPs, reversed roles (`current_primary`, `rebuild_standby`) |
| Browser destination | Client DNS or `/etc/hosts` | `PRIMARY_IP todo.test notes.test`; change to promoted host after failover |
| Firewall | VM firewalld and manual hypervisor fencing/quarantine | Replace source/destination IPs in the rules; HTTPS 8443 from client, replication 5432-5434 from peer |

With NAT, check the source address seen by the destination. Our primary saw
`192.168.0.100` in `SSH_CLIENT`, different from the ThinkPad's own LAN address.
Manual reset uses the Proxmox node Shell and requires no hypervisor SSH.

Pod DNS names (`todo-app`, `notes-app`, `todo-postgres`, `notes-postgres`,
`keycloak`, `keycloak-postgres`) stay unchanged. Editing
inventory does not readdress running databases or update persisted DR config.
These instructions prepare a clean topology; changing the IPs of an existing
replicated pair requires a separate maintenance plan.

## 1. Clean-host evidence

- **Where:** Client/build host for approval and topology; Proxmox node Shell for manual reset; both guests for checks.
- **Preconditions:** Explicit NEW/reset approval, identified disposable VMs and exact clean snapshots. CONTINUATION starts at its verified pending phase, not here.
- **PASS:** Distinct expected identities, enforcing security, rootless runtime and clean Todo baseline.
- **Evidence:** Reset approval, VM/snapshot IDs, addresses, security and empty-state output.
- **STOP if:** Wrong identity, leftover Todo state, uncertain reset scope or unexpected external firewall state.

On each VM, record:

```bash
hostname
cat /etc/machine-id
ip -brief -4 address
getenforce
systemctl is-active sshd firewalld fapolicyd qemu-guest-agent
loginctl show-user "$USER" -p Linger
podman info --format 'Rootless={{.Host.Security.Rootless}} GraphRoot={{.Store.GraphRoot}}'
python3 -c 'import jinja2, yaml; print("jinja2/pyyaml ok")'
df -h "$HOME"
free -m
podman ps -a
podman volume ls
podman secret ls
podman network ls
find "$HOME/.config/containers/systemd" -type f \( -name 'todo*' -o -name 'notes*' -o -name 'keycloak*' -o -name 'shared-proxy*' -o -name 'app-network*' \) -print
ls -ld "$HOME/.config/todo" /opt/todo/bin/app_dr.py /opt/todo/bin/app_backup.py
```

For a clean baseline, require distinct machine IDs and expected hostnames/IPs,
no Todo, Notes or Keycloak containers, volumes, secrets, no `app-network`
network or Quadlet files, no Todo config
directory and no installed DR/backup tools. Missing paths in the last two
commands are expected; distinguish absence from access errors. Unrelated Podman
resources are outside these name-scoped checks. Inspect external firewall state
separately; snapshot rollback does not reset it.

From the Proxmox node Shell, also check each VM's own firewall, independent of
the guest checks above:

```bash
pvesh get /nodes/localhost/qemu/<PRIMARY_VMID>/firewall/options
pvesh get /nodes/localhost/qemu/<STANDBY_VMID>/firewall/options
```

Require `enable: 0` on both. A prior drill's quarantine profile (`enable: 1`
with `policy_in`/`policy_out: DROP`) lives in the Proxmox configuration, not
the VM disk snapshot, and survives a snapshot rollback unnoticed; left in
place it silently blocks HTTPS, SSH or replication traffic later in the run
without any Todo-state symptom above. If found, clear or disable it explicitly
(`pvesh set /nodes/localhost/qemu/<VMID>/firewall/options -enable 0`) and
record why it was present before continuing.

Pass when identities differ, security services are active, SELinux is enforcing,
Podman is rootless, Jinja2 and PyYAML import successfully, user systemd is
available and no Todo state exists. A VM
snapshot is a lab convenience, not part of the application recovery model.
Container creation times close to VM boot do not prove a clean restore: any
Todo container, volume or secret means the selected snapshot is not this
baseline. Restore both matching pre-install snapshots instead of manually
deleting visible resources, because systemd, firewall and policy state must be
reset as well.

## 2. Build and stage artifacts

- **Where:** Client/build host; then both guests over verified SSH.
- **Preconditions:** Phase 1 passed; selected clean Git revision; build prerequisites available.
- **PASS:** Both archives and both extracted packages identify the same clean revision; checksums pass.
- **Evidence:** Full revision, source_state, archive checksums and guest VERSION output.
- **STOP if:** Dirty source, mismatched versions, checksum failure or unverified SSH identity.

On the connected build host:

```bash
test -z "$(git status --porcelain)"
git rev-parse HEAD
deploy/offline/build-bundle.sh
deploy/scripts/build-operations-package.sh
cd dist
sha256sum -c todo-offline-m12.tar.gz.sha256
sha256sum -c todo-operations.tar.gz.sha256
tar -xOf todo-offline-m12.tar.gz todo-offline-m12/VERSION
tar -xOf todo-operations.tar.gz todo-operations/VERSION
```

After extraction, run `sha256sum -c SHA256SUMS` inside each package before
running packaged tools. Both `VERSION` files must contain the same full revision and
`source_state=clean`. Transfer each archive and checksum through a trusted path.
On both VMs:

```bash
cd "$HOME"
sha256sum -c todo-offline-m12.tar.gz.sha256
sha256sum -c todo-operations.tar.gz.sha256
tar -xzf todo-offline-m12.tar.gz
tar -xzf todo-operations.tar.gz
cat todo-offline-m12/VERSION
cat todo-operations/VERSION
```

Stop if an extracted package and its archive identify different revisions.

## 3. Initial single-host deployment

- **Where:** Initial primary via SSH; client/build host for trust and browser tests; Proxmox node Shell for reboot.
- **Preconditions:** Phase 2 passed; initial primary identity confirmed; client source IP known.
- **PASS:** Healthy app/identity/database, trusted HTTPS and real authenticated browser flow; marker/CA survive reboot; repeat preserves definitions, credentials and running containers.
- **Evidence:** Installer results, browser results with no skips or TLS bypass, Todo ID/title, CA fingerprint and boot IDs.
- **STOP if:** Skipped login test, TLS error, missing marker, failed services or non-idempotent repeat.

On `todo-primary`:

```bash
cd "$HOME/todo-offline-m12"
# First register the verified installer Python files using the exact-file
# trust recipe in deploy/offline/README.md (fapolicyd remains active).
sh ./preflight.sh
sh ./install.sh --publish-address 192.168.0.102
```

On `todo-primary`, inspect `sudo firewall-cmd --get-active-zones` and use the
zone containing its LAN interface (the lab uses `public`). Allow HTTPS only
from the client. Confirm its actual source IPv4 address before entering it:

```bash
read -rp "Client IPv4 address: " TODO_CLIENT_IP
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule="rule family=\"ipv4\" source address=\"${TODO_CLIENT_IP}/32\" destination address=\"192.168.0.102\" port port=\"8443\" protocol=\"tcp\" accept"
sudo firewall-cmd --reload
```

On the client laptop, map both `todo.test` and `notes.test` to `192.168.0.102` and install the new
public demo CA as described in `docs/TLS.md`. A prior drill may have left
`todo.test` pointing to `.108` and an obsolete CA in the trust store.

Require all seven long-running services, no failed user units, nginx image identity,
valid nginx configuration, health and readiness of both apps, and Keycloak discovery:

```bash
systemctl --user is-active \
  shared-proxy.service \
  todo-app.service \
  notes-app.service \
  keycloak.service \
  todo-postgres.service \
  notes-postgres.service \
  keycloak-postgres.service
systemctl --user --failed --no-pager
podman image inspect localhost/todo-proxy:m12 \
  --format '{{index .Labels "io.todo.proxy"}}'
podman exec nginx nginx -t -c /etc/todo-nginx/nginx.conf
curl --fail -H 'Host: todo.test' http://127.0.0.1:8080/health
curl --fail -H 'Host: todo.test' http://127.0.0.1:8080/ready
curl --fail -H 'Host: notes.test' http://127.0.0.1:8080/health
curl --fail -H 'Host: notes.test' http://127.0.0.1:8080/ready
curl --fail -H 'Host: todo.test' http://127.0.0.1:8080/auth/realms/todo/.well-known/openid-configuration
```

Export only the public demo root, trust it on the client, verify HTTPS for both
hostnames, run the Todo and Notes Playwright flows and the Todo/Notes SSO flow,
and leave one persistent authenticated Todo marker and one persistent
authenticated Notes marker.
Use `curl` without `-k` and browser tests with
`E2E_IGNORE_HTTPS_ERRORS=false`. Configure the actual Chromium trust database
as described below. The development `run-e2e.sh` enables TLS
exceptions and is not the acceptance command. All three real Keycloak browser
flows (Todo, Notes and the multi-app SSO test) must run; adapter tests with a
test double do not replace them.

### Client trust and real browser verification

`deploy/scripts/trust-serving-ca.sh user@serving-host` performs the fingerprint
comparison and both trust-store updates below in one step, for operators who
already understand the manual sequence. It changes nothing that the commands
below do not already do explicitly.

On the client, inspect the existing `todo.test` and `notes.test` mappings and
replace only those entries with the current serving host's IP. Initially this is `.102`; after
promotion it is `.108`. Do not leave two competing mappings. Retrieve only the
public CA over verified SSH and compare its SHA-256 with the serving host's copy.
On the initial host the public CA is in `nginx:/var/lib/todo-tls/ca.crt`;
the promoted application role also exports it to `~/.config/todo/todo-nginx-root.crt`.
Never export the private key. See [TLS](TLS.md) for the trust model.

Example from the client, using the verified current serving address:

```bash
read -rp "Current serving host IPv4: " TODO_SERVING_IP
ssh -o StrictHostKeyChecking=yes "gunstein@${TODO_SERVING_IP}" \
  'podman exec nginx cat /var/lib/todo-tls/ca.crt' > /tmp/todo-public-root.crt
openssl x509 -in /tmp/todo-public-root.crt -noout -fingerprint -sha256
```

Compare that fingerprint with `podman exec nginx openssl x509 -in
/var/lib/todo-tls/ca.crt -noout -fingerprint -sha256` on the serving host before
import. On the Debian-family test client, after reviewing the existing target:

```bash
sudo cp /tmp/todo-public-root.crt /usr/local/share/ca-certificates/todo-nginx-root.crt
sudo update-ca-certificates
curl --fail https://todo.test:8443/ready
curl --fail https://notes.test:8443/ready
curl --fail https://todo.test:8443/auth/realms/todo/.well-known/openid-configuration
```

Require the stable issuer `https://todo.test:8443/auth/realms/todo`.
Use the client's native trust mechanism on other platforms.

System trust and Chromium trust are separate on this Linux test client.
Retrieve only the public CA via verified SSH from the current serving host and compare
its hash before import. Never import private keys. Install `libnss3-tools` on
the client/build host if needed. Chromium uses the existing `~/.pki/nssdb`, or for newer
versions the default `~/.local/share/pki/nssdb` when the old database is absent.
Inspect the actual database first; do not overwrite another certificate.

The tested import used a unique fingerprint-derived nickname and TLS-CA trust
only (`C,,`). Substitute the reviewed database, nickname and public CA path:

```bash
certutil -L -d sql:/path/to/nssdb
certutil -A -d sql:/path/to/nssdb -n todo-lab-ca-FINGERPRINT -t 'C,,' -i /path/to/public-root.crt
```

The authenticated test requires the existing secure test-user provisioning
and E2E credentials in memory; a skipped test is not PASS. Do not record those
credentials in this guide or shell history. Remove only the exact lab nickname
when trust is retired using `certutil -D -d sql:/path/to/nssdb -n NAME`.
See [Chromium's certificate documentation](https://chromium.googlesource.com/chromium/src/+/master/docs/linux/cert_management.md).

On the client, use the selected source revision and an environment with
`todo-backend/requirements-e2e.txt` and Chromium installed, for example
`python3 -m venv todo-backend/.venv` followed by
`todo-backend/.venv/bin/python -m pip install -r todo-backend/requirements-e2e.txt`. Provision `testuser` with
its complete profile through the trusted Keycloak admin UI (email, first and last
name, no required actions, non-temporary password), or use the existing
`e2e/provision_user.py` on the serving host with credentials supplied only in
memory. The Keycloak administrator password is generated at install time; read
it on the serving host only into a process environment with
`podman secret inspect --showsecret --format '{{.SecretData}}' keycloak-admin-password`
and never print or store it. Never enable direct password grants for the
frontend clients.

Run all three browser flows from the client, entering the test password locally:

```bash
(
  read -rsp "E2E password for testuser: " E2E_PASSWORD
  echo
  export E2E_PASSWORD E2E_USERNAME=testuser
  E2E_BASE_URL=https://todo.test:8443 E2E_IGNORE_HTTPS_ERRORS=false \
    todo-backend/.venv/bin/python -m pytest e2e/test_todo_flow.py --browser chromium -q
  E2E_NOTES_URL=https://notes.test:8443 E2E_IGNORE_HTTPS_ERRORS=false \
    todo-backend/.venv/bin/python -m pytest e2e/test_notes_flow.py --browser chromium -q
  E2E_MULTI_APP=1 E2E_CA_FILE=/tmp/todo-public-root.crt E2E_IGNORE_HTTPS_ERRORS=false \
    todo-backend/.venv/bin/python -m pytest e2e/test_multi_app.py -q
)
```

Require the Todo, Notes and multi-app SSO tests all passed and zero skipped tests. Repeat this browser check after
application failover and final reboots, updating trust for a newly created CA.
The tests delete their own rows; create one separate authenticated persistent
marker in each app through the UI and record both IDs/titles for replication and
reboot checks.

Reboot the VM. Repeat the seven-service and nginx configuration checks above;
verify both markers and unchanged TLS CA fingerprint in `todo-nginx-data`, then rerun
`sh ./install.sh --publish-address 192.168.0.102`. Pass when the second
deployment preserves the rendered definitions, secret IDs, container IDs and CA.
Record their before/after values; the Python CLI prints one JSON result.

## Before phase 4: trust, inventories and sudo

- **Where:** Both VMs through SSH from the client/build host.
- **Preconditions:** Phases 1-3 passed. Explicit operator approval for passwordless sudo during the run.
- **PASS:** app-ops is trusted on the controller, both inventories match the hosts, `sudo -n true` works on both VMs, and app-ops refused cleanly before the approval took effect.
- **Evidence:** Trust output, both inventories, the refusal output, the sudoers file and `sudo -n true` on both VMs.
- **STOP if:** A password prompt appears anywhere, or the refusal changed anything.

The controller is the VM that runs app-ops, marked `local: true` in the
inventory; it reaches the other VM over SSH with `BatchMode=yes` and
`StrictHostKeyChecking=yes`. The initial primary is the controller until
promotion; the promoted host is the controller from phase 7.

**Trust app-ops on each controller.** With fapolicyd active, app-ops is project
Python that must be trusted before it runs. On the initial primary now, and on
the initial standby before phase 7:

```bash
cd "$HOME/todo-operations"
sha256sum -c SHA256SUMS
sudo sh deploy/scripts/trust-files.sh trust todo \
  "$PWD"/deploy/ops/app_ops/*.py "$PWD"/deploy/installer/app_installer/*.py
```

This is the one step where sudo still asks for a password, typed by the
operator. Require `changed` the first time and `unchanged` when repeated. The
trust applies to these exact files; repeat it after replacing the package.

**Inventories.** Two small YAML files in `$HOME/todo-operations`. Host names must
match `hostname` on each VM exactly, and addresses must match `ip -4 address`;
app-ops checks both and refuses a mismatch. `initial.yaml` on the initial
primary:

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
phase 2. Every app-ops command below starts from the package directory with:

```bash
cd "$HOME/todo-operations"
export PYTHONPATH="$PWD/deploy/ops" PYTHONDONTWRITEBYTECODE=1
```

Each command prints one JSON line on success, and one `app-ops:` error line on
stderr with exit 1 on failure. Record both.

**Passwordless sudo, and why it needs approval.** app-ops runs privileged steps
as `sudo -n`; it never reads, sends or stores a password. Both VMs need
passwordless sudo for the service user: the controller trusts and stages files
locally, and the other VM installs them. That gives the service user root
without a password, so whoever can run commands as that user, an agent
included, can do anything as root on that VM. Approving it is the operator's
access decision. Grant it for the run only, as a separate file that is easy to
see and remove.

First prove the refusal. On the initial primary, before any sudoers change,
with the trust and `initial.yaml` in place:

```bash
sudo -k
python3 -m app_ops --inventory initial.yaml preflight-standby; echo "exit=$?"
```

Require exit 1 and a message naming passwordless sudo (NOPASSWD) for the
user, and no password prompt. Nothing changed: the first privileged step was
refused. After approval, on each VM, as the operator who knows the sudo
password:

```bash
sudo visudo -f /etc/sudoers.d/90-app-ops-acceptance
```

Enter exactly one line, with the real service user:

```text
gunstein ALL=(root) NOPASSWD: ALL
```

Then check, and remove the file again at the end of phase 11:

```bash
sudo cat /etc/sudoers.d/90-app-ops-acceptance
sudo -k
sudo -n true && echo "passwordless sudo ok"
```

## 4. Initial standby bootstrap

- **Where:** Initial primary is the app-ops controller; standby is remote target; Proxmox node Shell reboots standby.
- **Preconditions:** Phase 3 passed; verified controller-to-standby SSH; dedicated guest replication firewall rule.
- **PASS:** For each of the three databases: streaming async, zero lag, active usable slot, read-only standby; both markers persist.
- **Evidence:** Role/LSN outputs and slot state per database, marker queries, app-ops JSON results and standby boot IDs.
- **STOP if:** Failed preflight, role mismatch, unusable slot, lag or absent marker.

On `todo-primary`, require passwordless primary-to-standby SSH:

```bash
ssh -o BatchMode=yes gunstein@192.168.0.108 hostname
```

If a snapshot restore changed or removed SSH state, verify the standby host-key
fingerprint through an independently verified connection (for example the
client/build host's already trusted SSH connection), then follow
[STANDBY-ARCHITECTURE.md](../deploy/ops/STANDBY-ARCHITECTURE.md) to
install primary's public automation key. Do not weaken host-key checking.

Prove that the preflight refuses without the replication firewall rule, and
names it:

```bash
python3 -m app_ops --inventory initial.yaml preflight-standby; echo "exit=$?"
```

Require exit 1 and a message naming the missing rule. Then allow only standby
to reach the three initial replication endpoints:

```bash
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family="ipv4" source address="192.168.0.108/32" destination address="192.168.0.102" port port="5432-5434" protocol="tcp" accept'
sudo firewall-cmd --reload
```

Run:

```bash
python3 -m app_ops --inventory initial.yaml preflight-standby
python3 -m app_ops --inventory initial.yaml bootstrap-standby
python3 -m app_ops --inventory initial.yaml replication-status
python3 -m app_ops --inventory initial.yaml replication-status
```

`replication-status` must report `{"changed": false}` both times. The standby
secrets travel stdin to stdin and are never written on the controller:
`find "$HOME/todo-operations" -newer SHA256SUMS -type f` lists only the two
inventory files. Pass when, for every database, primary reports `streaming|async`, the slot is
active and usable, measured lag is zero, and standby reports recovery with
matching receive/replay LSNs. Verify both markers directly on the standby
databases, for example:

```bash
podman exec todo-postgres psql --username todo --dbname todo --command "SELECT id, title FROM todos ORDER BY id;"
podman exec notes-postgres psql --username notes --dbname notes --command "SELECT id, title FROM notes ORDER BY id;"
```

Reboot standby and require recovery plus streaming to resume for all three.

## 5. Local DR tool

- **Where:** Initial primary for app-ops; standby for local DR status; Proxmox node Shell for quarantine rehearsal.
- **Preconditions:** Phase 4 passed; explicit approval before Guest Agent/security opt-ins.
- **PASS:** Correct DR config, read-only healthy standby, zero apply lag; installer repeat `{"changed": false}`; quarantine tested and normal operation restored.
- **Evidence:** Install/status output and quarantine stop, IPv4/IPv6, restricted SSH and restoration evidence.
- **STOP if:** Trust/policy error, untested quarantine, failed stop or inability to restore initial healthy replication.

Install the DR tool and its exact-file trust, twice:

```bash
python3 -m app_ops --inventory initial.yaml install-dr-tool
python3 -m app_ops --inventory initial.yaml install-dr-tool
```

Require `{"changed": true}` and then `{"changed": false}`. app-ops keeps
`fapolicyd` active, trusts only the verified source and the root-owned file
under `/opt/todo/bin`, and keeps the non-secret
configuration under `~/.config/todo`. Require, for each database, healthy
standby, read-only database, reachable primary and zero local apply lag:

```bash
python3 /opt/todo/bin/app_dr.py status
```

Zero local apply lag means nothing received is left to replay. Right after a
standby restart the receive LSN can be *behind* the replay LSN, because
PostgreSQL restarts the walreceiver at the start of the current WAL segment;
`app_dr.py status` then reports 0 bytes with a note. That is expected.

Prepare and rehearse [Proxmox quarantine](PROXMOX-QUARANTINE.md) now, while
initial primary is still the authorized writable node. Verify restored normal
operation and streaming before proceeding to fencing.

## 6. Fence and promote

- **Where:** Proxmox node Shell for fencing; initial standby for promotion.
- **Preconditions:** Replicated persistent marker; tested quarantine; independent fencing evidence and explicit promotion approval.
- **PASS:** All three databases report f|off after one group promotion, write validation passes and all markers remain.
- **Evidence:** Hypervisor fencing output, approval, preflight/status and marker IDs.
- **STOP if:** Any fencing uncertainty, reachable old DB, nonzero local apply lag or failed promotion. Never blindly retry.

Create a persistent pre-failover marker in Todo and in Notes and verify both on standby. Fence
`todo-primary` at the virtualization layer. Its database endpoint must be
unreachable before continuing.

On standby:

```bash
python3 /opt/todo/bin/app_dr.py preflight \
  --confirm-primary-fenced 'todo-primary is fenced'
python3 /opt/todo/bin/app_dr.py promote \
  --confirm-primary-fenced 'todo-primary is fenced' \
  --confirm-promotion todo-standby
python3 /opt/todo/bin/app_dr.py status
```

Promotion acts on the complete group: preflight refuses unless every database
is healthy and caught up, and a partial failure is recorded and blocks blind
retry. Verify local writable state and a rolled-back write on the promoted host:

```bash
podman exec todo-postgres psql --username todo --dbname todo --set ON_ERROR_STOP=1 \
  --command "BEGIN; INSERT INTO todos (title, completed) VALUES ('promotion write probe', false); ROLLBACK;"
podman exec notes-postgres psql --username notes --dbname notes --set ON_ERROR_STOP=1 \
  --command "BEGIN; INSERT INTO notes (title) VALUES ('promotion write probe'); ROLLBACK;"
```

Pass when every database reports `f|off`, the rolled-back writes succeed and all
markers remain. Keep old primary fenced.

## 7. Application failover

- **Where:** Promoted host for deployment; client/build host for routing/trust/browser; Proxmox node Shell for reboot.
- **Preconditions:** Phase 6 passed; old primary fenced; existing secrets and matching image archives available.
- **PASS:** Healthy application, stable production issuer, real login and persistent marker; `{"changed": false}` repeat; reboot preserves CA/data.
- **Evidence:** app-ops JSON results, trusted browser results, marker/CA and boot IDs.
- **STOP if:** Missing secrets/images, TLS or login failure, unexpected role/bootstrap activity or marker loss.

On the promoted host, trust app-ops and write `recovery.yaml` as described
[before phase 4](#before-phase-4-trust-inventories-and-sudo). Then:

```bash
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family="ipv4" source address="192.168.0.100/32" destination address="192.168.0.108" port port="8443" protocol="tcp" accept'
sudo firewall-cmd --reload
python3 -m app_ops --inventory recovery.yaml deploy-promoted-application
```

This command runs only on the promoted host itself; from any other controller
it refuses before it changes anything.

Map `todo.test` and `notes.test` to `.108` on the client and install the exported public nginx
root. Require system-trust HTTPS, health/readiness, stable issuer
`https://todo.test:8443/auth/realms/todo`, replicated data, browser login and
SSO tests, and a persistent authenticated failover marker in each app.

Rerun `deploy-promoted-application` and require `{"changed": false}`. Reboot promoted host and verify all
seven workload services listed in phase 3, writable PostgreSQL,
`podman exec nginx nginx -t -c /etc/todo-nginx/nginx.conf`, marker data
and unchanged CA hash. Each app pod shares loopback between frontend and backend,
but the proxy reaches both over DNS; frontend serves HTTP only and holds no TLS keys.

## 8. Backup and isolated PITR

- **Where:** Current primary via SSH; Proxmox node Shell for reboot.
- **Preconditions:** Phase 7 passed; old primary fenced; sufficient disk; record any existing restore state.
- **PASS:** For Todo and Notes: before-row only in restored view, both live rows retained, restore network-disabled/read-only; all three archives work after reboot.
- **Evidence:** Backup and restore-point names, comparison, cleanup output, archive counters, capacity and boot IDs.
- **STOP if:** Unverified backup, missing WAL, wrong restore target, archive failure or low space.

Install the tool and its exact-file trust, and configure archiving:

```bash
python3 -m app_ops --inventory recovery.yaml configure-backup
```

Without `--app`, `status`, `create` and `mark` act on all three databases and
prefix each line with the database name. Require, for each, writable database,
`archive_mode=on`, `archive_timeout=1h`, an exact archived segment and zero
failures:

```bash
python3 /opt/todo/bin/app_backup.py status
python3 /opt/todo/bin/app_backup.py create
```

Record the returned backup name for each database; they are separate backups.
On the current primary, create a before-row in Todo and Notes, archive one
named restore point in every database, then create an after-row in both.
Disposable restore operations require an explicit `--app`, given before the
subcommand:

```bash
podman exec todo-postgres psql --username todo --dbname todo --set ON_ERROR_STOP=1 \
  --command "INSERT INTO todos (title, completed) VALUES ('PITR before restore point', false);"
podman exec notes-postgres psql --username notes --dbname notes --set ON_ERROR_STOP=1 \
  --command "INSERT INTO notes (title) VALUES ('PITR before restore point');"
python3 /opt/todo/bin/app_backup.py mark --name acceptance_before_after
podman exec todo-postgres psql --username todo --dbname todo --set ON_ERROR_STOP=1 \
  --command "INSERT INTO todos (title, completed) VALUES ('PITR after restore point', false);"
podman exec notes-postgres psql --username notes --dbname notes --set ON_ERROR_STOP=1 \
  --command "INSERT INTO notes (title) VALUES ('PITR after restore point');"
python3 /opt/todo/bin/app_backup.py --app todo restore \
  --backup base-YYYYMMDDTHHMMSSZ --target acceptance_before_after
python3 /opt/todo/bin/app_backup.py --app todo restore-status
podman inspect todo-postgres-restore --format '{{.HostConfig.NetworkMode}}'
podman exec todo-postgres-restore psql --username todo --dbname todo \
  --command "SELECT id, title FROM todos WHERE title LIKE 'PITR % restore point' ORDER BY id;"
podman exec todo-postgres psql --username todo --dbname todo \
  --command "SELECT id, title FROM todos WHERE title LIKE 'PITR % restore point' ORDER BY id;"
python3 /opt/todo/bin/app_backup.py --app notes restore \
  --backup base-YYYYMMDDTHHMMSSZ --target acceptance_before_after
python3 /opt/todo/bin/app_backup.py --app notes restore-status
podman inspect notes-postgres-restore --format '{{.HostConfig.NetworkMode}}'
podman exec notes-postgres-restore psql --username notes --dbname notes \
  --command "SELECT id, title FROM notes WHERE title LIKE 'PITR % restore point' ORDER BY id;"
podman exec notes-postgres psql --username notes --dbname notes \
  --command "SELECT id, title FROM notes WHERE title LIKE 'PITR % restore point' ORDER BY id;"
```

Replace each backup placeholder with that database's recorded verified backup. Require
`recovery|paused|read_only = t|t|on`, network `none`, only the before-row in
restored data and both rows in live data. Never substitute a live volume as a
restore target. Existing disposable restore state is a STOP condition; inspect
it using troubleshooting before authorizing any replacement.

After explicit cleanup approval:

```bash
python3 /opt/todo/bin/app_backup.py --app todo cleanup-restore --confirm todo-postgres-restore
python3 /opt/todo/bin/app_backup.py --app notes cleanup-restore --confirm notes-postgres-restore
```

Verify that only disposable restore resources disappeared; live data and all
three backup volumes must remain. Record the comparison and cleanup evidence. The
backup is on the same VM and does not protect against VM/host loss.

Rerun `configure-backup` and require `{"changed": false}`. Reboot current primary and verify
application readiness, writable database, backup persistence, zero archive
failures and bounded WAL use.

## 9. Rebuild old primary as standby

- **Where:** Proxmox node Shell for isolated boot/quarantine; current primary runs app-ops.
- **Preconditions:** Phase 8 passed; reviewed backup/PITR evidence; old primary remains fenced; explicit reseed approval.
- **PASS:** Authenticated replication check precedes deletion for every database; all three rebuilt databases are read-only and streaming with zero lag; new authenticated markers replicate.
- **Evidence:** Approvals, STOPPED and active firewall rules, app-ops JSON results, slot/role checks and marker ID.
- **STOP if:** Any failed gate or partial rebuild: preserve state, diagnose, never repeat destructive reseed blindly.

Use the quarantine route already prepared and rehearsed in phase 5:
[PROXMOX-QUARANTINE.md](PROXMOX-QUARANTINE.md). Start the old VM with every
network link disconnected. Execute the labelled stop helper directly through
Guest Agent, require exited=1, exitcode=0 and STOPPED, then inspect applied
IPv4/IPv6 quarantine rules before reconnecting restricted SSH.
A helper installation alone is not proof that quarantine works.

Stopped services may be inactive or failed only with zero MainPID/ControlPID
and no running user containers; preserve failure evidence. Remove the old
inbound replication rule on .102. On current primary allow only .102 to reach
.108:5432-5434. Establish verified key-based SSH from current primary to rebuild
host. Enable only the inspected Proxmox outbound replication exception.

First prove the confirmation gate. Wrong confirmations must refuse before
anything on the rebuild host changes:

```bash
python3 -m app_ops --inventory recovery.yaml preflight-standby-rebuild \
  --confirm-fenced todo-primary --confirm-reseed todo-primary; echo "exit=$?"
```

Require exit 1 and a message saying both exact confirmations are required.
Then run the real read-only preflight. It does not test the replication path:
until the rebuild publishes the primary's ports, the quarantine firewall can
drop the refusal, so an open path and a blocked one look the same.

```bash
python3 -m app_ops --inventory recovery.yaml preflight-standby-rebuild \
  --confirm-fenced "todo-primary is fenced" --confirm-reseed todo-primary
```

Only after it reports `{"changed": false}`, run the rebuild once:

```bash
python3 -m app_ops --inventory recovery.yaml rebuild-standby \
  --confirm-fenced "todo-primary is fenced" --confirm-reseed todo-primary
```

It first checks `sudo -n` on both VMs, so missing sudo stops the run before
primary configuration changes or deletion of the old database. It repeats
every preflight gate, publishes the redundancy configuration on the current
primary, and then requires a TCP connection from `.102` to every replication
port; `replication port ... is not reachable` means a firewall step above is
missing, and nothing was deleted. Never rerun it after a failure. Pass when,
for every database, authenticated `IDENTIFY_SYSTEM` precedes volume deletion,
a fresh base backup initializes `.102`, and final state is `streaming|async`;
from `.102`, ports 5432-5434 on `.108` now connect.

Run `python3 -m app_ops --inventory recovery.yaml cluster-status`; it reports every registered
database. Create an authenticated Todo through `todo.test` and an authenticated
Note through `notes.test`, and verify both directly on rebuilt standby.

## 10. Final reboot sequence

- **Where:** Proxmox node Shell for one reboot at a time; current primary for cluster checks; client/build host for HTTPS.
- **Preconditions:** Phase 9 passed, both roles independently verified and streaming healthy.
- **PASS:** Roles/data/CA/backup survive, application healthy, streaming zero lag and no failed units.
- **Evidence:** Each before/after boot ID, fresh cluster status, health, TLS/issuer and markers.
- **STOP if:** Standby not recovered, replication unhealthy or any data/TLS failure. Do not reboot the other host.

1. Reboot only rebuilt standby.
2. Require `t|on` for all three databases, only the three PostgreSQL services and resumed streaming.
3. Run `cluster-status`.
4. Reboot only current primary.
5. Require all seven workload services from phase 3,
   `podman exec nginx nginx -t -c /etc/todo-nginx/nginx.conf`, `f|off|on|1h` for
   every database, persistent backups, unchanged TLS CA and readiness of both apps.
6. Run `cluster-status` again.
7. Verify trusted HTTPS for both hostnames, stable issuer and all markers from the client.
8. Record backup/WAL size and free disk.

Pass only when final status reports writable primary, healthy archiving,
`streaming|async`, active usable slot, zero measured lag, read-only recovery
standby, and healthy application through trusted HTTPS.

## 11. Final Kube-runtime gate

- **Where:** Client/build host collects evidence; both guests supply fresh checks.
- **Preconditions:** All previous phases passed on the recorded revision; any repair recorded.
- **PASS:** CLEAN PASS only for complete unchanged-revision evidence; otherwise record repaired functional pass or incomplete status.
- **Evidence:** Final role/topology record, phase outputs, real browser results and exact verdict with deviations.
- **STOP if:** Missing evidence, skipped authenticated tests, unresolved failure or revision drift; do not mark accepted.

There is no migration stage in the normal DR path. Clean deployment,
standby bootstrap, promotion, backup and rebuild must already use these
workload services and pods:

```text
shared-proxy.service       shared-proxy pod (container: nginx)
todo-app.service           todo-app pod
notes-app.service          notes-app pod
keycloak.service           keycloak pod
todo-postgres.service      todo-postgres pod
notes-postgres.service     notes-postgres pod
keycloak-postgres.service  keycloak-postgres pod
```

Always obtain fresh cluster evidence directly:

```bash
python3 -m app_ops --inventory recovery.yaml cluster-status
```

Inspect reported lag and LSNs for every database in the JSON; exit 0 alone
does not prove zero lag or complete acceptance.
Require schema migrations applied by both init containers, healthy
backends/frontends, proxy DNS routing to `todo-app:8080`/`todo-app:8000` and
`notes-app:8080`/`notes-app:8000`, proxy-to-Keycloak DNS routing to
`keycloak:8080` through `app-network.network`, unchanged PostgreSQL identities,
persistent data, streaming replication and WAL archive health.

Use the sequential reboot evidence from phase 10; do not add another reboot.
After those boots, require `NRestarts=0` for `todo-app.service`,
`notes-app.service` and `shared-proxy.service`, no failed user
units, readiness, stable issuer and trusted browser E2E, including the
Notes and multi-app SSO tests, with no skipped tests.
CLEAN PASS requires every phase on the same clean revision, isolated PITR,
sequential final reboots and new authenticated Todo and Notes markers read on
rebuilt standby.
Record repairs as REPAIRED FUNCTIONAL PASS, preserving original failures.
Keep quarantine through verification. The stop helper is not a rebuilt-standby
management tool: it expects all seven registered workload units (including `shared-proxy.service`).

Finally remove the run's passwordless sudo on both VMs, and require a non-zero
exit; a run that leaves the file in place is not complete:

```bash
sudo rm /etc/sudoers.d/90-app-ops-acceptance
sudo -k
sudo -n true; echo "exit=$?"
```

CLEAN PASS also requires that no Ansible command was run, that no password was
typed into or read by app-ops, that every refusal check failed as described and
changed nothing, and that every repeat reported `{"changed": false}`.
Do not reset the working pair or repeat promotion/rebuild after the verdict.
