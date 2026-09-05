# Full lab acceptance

This is the canonical destructive acceptance test for the three-workload
Podman Kube implementation. Individual runbooks explain each operation; this
document defines order, evidence and pass criteria.

For the phase checklist and topology-based command generator, start with
[MANUAL-DR-QUICKSTART.md](MANUAL-DR-QUICKSTART.md). It never connects to Proxmox.

The procedure permanently destroys the old primary database during the final
rebuild. Run it only on disposable lab hosts with infrastructure fencing.

## Entry and command convention

For NEW, obtain reset approval and use this revision's clean baseline; historical
results below are not live state. For CONTINUATION, read the private phase record
and obtain fresh roles, fencing and runner status before the next action.
Neither path requires reading PROJECT.md or a previous chat.

The phase cards own the gates; commands immediately below each card implement
them. All guest commands run as the service user through SSH. Use the
client/build host (a ThinkPad in the documented lab) for transfers and browser
tests. Proxmox commands are operator actions in the node Shell, not guest console.

The commands below form a direct playbook/tool route. The quickstart's print-only
generator offers the guarded runner route for promotion/application/rebuild.
Choose and record one route before promotion; do not run both versions of a
destructive phase. When using the runner, create and verify the recovery inventory
before its first use, using the inventory preparation in phase 7. The runner state
is not a replacement for phase evidence. Direct execution does not update it.

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
firewalld, RPM-managed Ansible Core 2.14.18, user lingering, 4 GiB memory and
an 18 GiB home filesystem per VM.

| Runtime | Podman requirement |
|---|---|
| Accepted per-container Quadlet baseline | Rootless Podman 4.9.3 validated |
| Current grouped Podman Kube runtime | Rootless Podman 5.8.2 required; platform features tested |

The current runtime requires Podman 5.8.2 because its PostgreSQL `.kube` unit uses
`--no-pod-prefix` to preserve the operational container name.

## Acceptance rules

- Start from clean, independently identifiable hosts.
- Build both packages from one clean Git revision.
- Verify checksums before extraction and compare `VERSION` on both hosts.
- Never place secret values in a file, transcript or Git.
- Keep old primary fenced from promotion until its old services are stopped and
  its data is deliberately re-seeded.
- Never skip a read-only preflight.
- Never rerun a one-shot destructive workflow blindly after partial failure.
- Never reboot both final database nodes at the same time.

## Where to change VM addresses

Choose addresses before starting a clean drill. IPs printed here are lab
examples. Keep the service name `todo.test`: moving it to another IP does not
require changing the realm, frontend, certificate hostname or Helm manifests.

| Setting | Where to change it | What to enter |
|---|---|---|
| VM network | Guest OS or DHCP reservation | Fixed address per VM; verify with `ip -brief -4 address` |
| Initial HTTPS binding | Primary: `sh ./install.sh --publish-address PRIMARY_IP` | Primary's own IPv4, on every install/rerun |
| Replication and SSH | Primary's `todo-operations/ansible/inventory-initial.ini` | Replace example IPs `192.0.2.10` and `192.0.2.11`; standby needs both `ansible_host` and `todo_node_address` |
| Recovery/rebuild | Promoted host's `todo-operations/ansible/inventory-recovery.ini` | Same machine IPs, new role groups; rebuild target needs both address fields |
| Browser destination | Client DNS or `/etc/hosts` | `PRIMARY_IP todo.test`; change to promoted host after failover |
| Firewall | VM firewalld and manual hypervisor fencing/quarantine | Replace source/destination IPs in the rules; HTTPS from client, replication from peer |
| Optional lab controller | Ignored `lab-dr.local.toml`: `nodes.primary.address`, `nodes.standby.address` | Same VM IPs; this file does not configure Ansible or guest networking |

With NAT, check the source address seen by the destination. Our primary saw
`192.168.0.100` in `SSH_CLIENT`, different from the ThinkPad's own LAN address.
The lab-controller CLI still assumes automated Proxmox reset; manual-reset
acceptance follows this runbook and requires no hypervisor SSH.

Container DNS names (`todo-postgres`, `todo-keycloak`) stay unchanged. Editing
inventory does not readdress running databases or update persisted DR config.
These instructions prepare a clean topology; changing the IPs of an existing
replicated pair requires a separate maintenance plan.

## 1. Clean-host evidence

- **Where:** Client/build host for approval and topology; Proxmox node Shell for manual reset; both guests for checks.
- **Preconditions:** Explicit NEW/reset approval, identified disposable VMs and exact clean snapshots. CONTINUATION starts at its verified pending phase, not here.
- **Command:** Perform the approved manual reset; run the guest observations below and inspect external VM firewall/link state separately.
- **PASS:** Distinct expected identities, enforcing security, rootless runtime and clean Todo baseline.
- **Evidence:** Reset approval, VM/snapshot IDs, addresses, security and empty-state output.
- **STOP if:** Wrong identity, leftover Todo state, uncertain reset scope or unexpected external firewall state.
- **Next:** Phase 2.

On each VM, record:

```bash
hostname
ip -brief -4 address
getenforce
systemctl is-active sshd firewalld fapolicyd qemu-guest-agent
loginctl show-user "$USER" -p Linger
podman info --format 'Rootless={{.Host.Security.Rootless}} GraphRoot={{.Store.GraphRoot}}'
ansible-playbook --version | head -1
df -h "$HOME"
podman ps -a
podman volume ls
podman secret ls
```

Pass when identities differ, security services are active, SELinux is enforcing,
Podman is rootless, user systemd is available and no Todo state exists. A VM
snapshot is a lab convenience, not part of the application recovery model.
Container creation times close to VM boot do not prove a clean restore: any
Todo container, volume or secret means the selected snapshot is not this
baseline. Restore both matching pre-install snapshots instead of manually
deleting visible resources, because systemd, firewall and policy state must be
reset as well.

## 2. Build and stage artifacts

- **Where:** Client/build host; then both guests over verified SSH.
- **Preconditions:** Phase 1 passed; selected clean Git revision; build prerequisites available.
- **Command:** Build, checksum, transfer and inspect VERSION using the commands below.
- **PASS:** Both archives and both extracted packages identify the same clean revision; checksums pass.
- **Evidence:** Full revision, source_state, archive checksums and guest VERSION output.
- **STOP if:** Dirty source, mismatched versions, checksum failure or unverified SSH identity.
- **Next:** Phase 3.

On the connected build host:

```bash
test -z "$(git status --porcelain)"
git rev-parse HEAD
offline/build-bundle.sh
scripts/build-operations-package.sh
cd dist
sha256sum -c todo-offline-m12.tar.gz.sha256
sha256sum -c todo-operations.tar.gz.sha256
tar -xOf todo-offline-m12.tar.gz todo-offline-m12/VERSION
tar -xOf todo-operations.tar.gz todo-operations/VERSION
```

Both `VERSION` files must contain the same full revision and
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
- **Command:** Install and verify below, configure client trust, test real login, reboot and repeat install.
- **PASS:** Healthy app/identity/database, trusted HTTPS and real authenticated browser flow; marker/CA survive reboot; repeat changed=0.
- **Evidence:** Recaps, browser results with no skips or TLS bypass, Todo ID/title, CA fingerprint and boot IDs.
- **STOP if:** Skipped login test, TLS error, missing marker, failed services or non-idempotent repeat.
- **Next:** Phase 4. Do not rerun the initial installer after replication configuration.

On `todo-primary`:

```bash
cd "$HOME/todo-offline-m12"
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

On the client laptop, map `todo.test` to `192.168.0.102` and install the new
public demo CA as described in `docs/TLS.md`. A prior drill may have left
`todo.test` pointing to `.108` and an obsolete CA in the trust store.

Require all long-running services, no failed user units, nginx image identity,
valid nginx configuration, health, readiness and Keycloak discovery:

```bash
systemctl --user is-active \
  todo-postgres.service \
  todo-keycloak.service \
  todo-app.service
systemctl --user --failed --no-pager
podman image inspect localhost/todo-frontend:m12 \
  --format '{{index .Labels "io.todo.proxy"}}'
podman exec todo-frontend nginx -t
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
```

Export only the public demo root, trust it on the client, verify HTTPS, run both
Playwright flows and leave one persistent authenticated Todo marker.
Use `curl` without `-k` and browser tests with
`E2E_IGNORE_HTTPS_ERRORS=false`. Configure the actual Chromium trust database
as described in the quickstart. The development `run-e2e.sh` enables TLS
exceptions and is not the acceptance command. Both real Keycloak browser flows
must run; adapter tests with a test double do not replace them.

Reboot the VM. Verify services, marker data and TLS CA persistence, then rerun
`sh ./install.sh --publish-address 192.168.0.102`. Pass when the second
deployment reports `changed=0`.

## 4. Initial standby bootstrap

- **Where:** Initial primary is Ansible controller; standby is remote target; Proxmox node Shell reboots standby.
- **Preconditions:** Phase 3 passed; verified controller-to-standby SSH; dedicated guest replication firewall rule.
- **Command:** Run standby preflight, bootstrap and replication-status below; verify marker, reboot standby and recheck.
- **PASS:** Streaming async, zero lag, active usable slot, read-only standby and marker persistence.
- **Evidence:** Both role/LSN outputs, slot state, marker query, bootstrap recap and standby boot IDs.
- **STOP if:** Failed preflight, role mismatch, unusable slot, lag or absent marker.
- **Next:** Phase 5.

On `todo-primary`:

```bash
cd "$HOME/todo-operations"
cp ansible/inventory-initial.example.ini ansible/inventory-initial.ini
sed -i \
  -e 's/192\.0\.2\.10/192.168.0.102/g' \
  -e 's/192\.0\.2\.11/192.168.0.108/g' \
  ansible/inventory-initial.ini
ansible-inventory --inventory ansible/inventory-initial.ini --graph
ansible --inventory ansible/inventory-initial.ini todo_cluster -m ping
```

Before these Ansible commands, require passwordless primary-to-standby SSH:

```bash
ssh -o BatchMode=yes gunstein@192.168.0.108 hostname
```

If a snapshot restore changed or removed SSH state, verify the standby host-key
fingerprint through an independently verified connection (for example the
client/build host's already trusted SSH connection), then follow
`ansible/STANDBY-ARCHITECTURE.md` to
install primary's public automation key. Do not weaken host-key checking.

Allow only standby to reach the initial replication endpoint:

```bash
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family="ipv4" source address="192.168.0.108/32" destination address="192.168.0.102" port port="5432" protocol="tcp" accept'
sudo firewall-cmd --reload
```

Run:

```bash
ansible-playbook --inventory ansible/inventory-initial.ini \
  ansible/preflight-standby.yml
ansible-playbook --inventory ansible/inventory-initial.ini \
  ansible/bootstrap-standby.yml
ansible-playbook --inventory ansible/inventory-initial.ini \
  ansible/replication-status.yml
```

Pass when primary reports `streaming|async`, the slot is active and usable,
measured lag is zero, and standby reports recovery with matching receive/replay
LSNs. Create a persistent Todo on primary, verify it directly on standby, reboot
standby and require recovery plus streaming to resume.

## 5. Local DR tool

- **Where:** Initial primary for Ansible; standby for local DR status; Proxmox node Shell for quarantine rehearsal.
- **Preconditions:** Phase 4 passed; explicit approval before Guest Agent/security opt-ins.
- **Command:** Install/repeat DR tools below; prepare and rehearse the linked quarantine procedure before phase 6.
- **PASS:** Correct DR config, read-only healthy standby, zero apply lag; installer repeat changed=0; quarantine tested and normal operation restored.
- **Evidence:** Install/status output and quarantine stop, IPv4/IPv6, restricted SSH and restoration evidence.
- **STOP if:** Trust/policy error, untested quarantine, failed stop or inability to restore initial healthy replication.
- **Next:** Phase 6 only after rehearsal and restored replication.

Install both DR tools and exact-file trust through Ansible:

```bash
ansible-playbook --ask-become-pass \
  --inventory ansible/inventory-initial.ini \
  ansible/install-dr-tool.yml
```

The central role keeps `fapolicyd` active, trusts only the verified source and
the two root-owned files under `/opt/todo/bin`, and keeps the non-secret
configuration under `~/.config/todo`. Require healthy standby, read-only
database, reachable primary and zero local apply lag:

```bash
python3 /opt/todo/bin/todo_dr.py status
```

Rerun the installer and require no file or trust changes.
Prepare and rehearse [Proxmox quarantine](PROXMOX-QUARANTINE.md) now, while
initial primary is still the authorized writable node. Verify restored normal
operation and streaming before proceeding to fencing.

## 6. Fence and promote

- **Where:** Proxmox node Shell for fencing; initial standby for promotion.
- **Preconditions:** Replicated persistent marker; tested quarantine; independent fencing evidence and explicit promotion approval.
- **Command:** Verify old VM stopped, no HA restart, onboot=0 and all links disconnected; then run promotion checks below.
- **PASS:** New primary reports f|off, write validation passes and all markers remain.
- **Evidence:** Hypervisor fencing output, approval, preflight/status and marker IDs.
- **STOP if:** Any fencing uncertainty, reachable old DB, nonzero local apply lag or failed promotion. Never blindly retry.
- **Next:** Phase 7; old primary must remain fenced.

Create a persistent pre-failover marker and verify it on standby. Fence
`todo-primary` at the virtualization layer. Its database endpoint must be
unreachable before continuing.

On standby:

```bash
python3 /opt/todo/bin/todo_dr.py preflight \
  --confirm-primary-fenced 'todo-primary is fenced'
python3 /opt/todo/bin/todo_dr.py promote \
  --confirm-primary-fenced 'todo-primary is fenced' \
  --confirm-promotion todo-standby
python3 /opt/todo/bin/todo_dr.py status
```

Pass when PostgreSQL reports `f|off`, accepts a rolled-back transaction and all
markers remain. Keep old primary fenced.

## 7. Application failover

- **Where:** Promoted host for deployment; client/build host for routing/trust/browser; Proxmox node Shell for reboot.
- **Preconditions:** Phase 6 passed; old primary fenced; existing secrets and matching image archives available.
- **Command:** Configure recovery inventory and deploy below; switch client mapping/CA; test, repeat and reboot.
- **PASS:** Healthy application, stable production issuer, real login and persistent marker; changed=0 repeat; reboot preserves CA/data.
- **Evidence:** Recaps, trusted browser results, marker/CA and boot IDs.
- **STOP if:** Missing secrets/images, TLS or login failure, unexpected role/bootstrap activity or marker loss.
- **Next:** Phase 8.

On the promoted host:

```bash
cd "$HOME/todo-operations"
cp ansible/inventory-recovery.example.ini ansible/inventory-recovery.ini
sed -i \
  -e 's/192\.0\.2\.11/192.168.0.108/g' \
  -e 's/192\.0\.2\.10/192.168.0.102/g' \
  ansible/inventory-recovery.ini
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family="ipv4" source address="192.168.0.100/32" destination address="192.168.0.108" port port="8443" protocol="tcp" accept'
sudo firewall-cmd --reload
ansible-playbook --ask-become-pass --inventory ansible/inventory-recovery.ini \
  ansible/deploy-promoted-application.yml
```

Map `todo.test` to `.108` on the client and install the exported public nginx
root. Require system-trust HTTPS, health/readiness, stable issuer
`https://todo.test:8443/auth/realms/todo`, replicated data, browser login and a
persistent authenticated failover marker.

Rerun the playbook and require `changed=0`. Reboot promoted host and verify all
services, writable PostgreSQL, nginx, marker data and unchanged CA hash.

## 8. Backup and isolated PITR

- **Where:** Current primary via SSH; Proxmox node Shell for reboot.
- **Preconditions:** Phase 7 passed; old primary fenced; sufficient disk; record any existing restore state.
- **Command:** Configure archive, create verified backup and execute the isolated comparison below; cleanup only disposable state; repeat/reboot.
- **PASS:** Before-row only in restored view; both live rows retained; restore is network-disabled/read-only; archive works after reboot.
- **Evidence:** Backup and restore-point names, comparison, cleanup output, archive counters, capacity and boot IDs.
- **STOP if:** Unverified backup, missing WAL, wrong restore target, archive failure or low space.
- **Next:** Phase 9.

Install the tool and its exact-file trust through the playbook:

```bash
cd "$HOME/todo-operations"
ansible-playbook --ask-become-pass --inventory ansible/inventory-recovery.ini \
  ansible/configure-backup.yml
```

Require writable database, `archive_mode=on`,
`archive_timeout=1h`, an exact archived segment and zero failures:

```bash
python3 /opt/todo/bin/todo_backup.py status
python3 /opt/todo/bin/todo_backup.py create
```

Perform the full sequence in
[../ansible/BACKUP-PITR.md](../ansible/BACKUP-PITR.md):

1. insert `M15 before restore point`;
2. archive restore point `m15_before_after`;
3. insert `M15 after restore point`;
4. restore the recorded base backup to the point;
5. require isolated status `t|t|on`;
6. require only the before-row in restored data;
7. require both rows in live data;
8. remove only the fixed disposable restore resources.

Rerun configuration and require `changed=0`. Reboot current primary and verify
application readiness, writable database, backup persistence, zero archive
failures and bounded WAL use.

## 9. Rebuild old primary as standby

- **Where:** Proxmox node Shell for isolated boot/quarantine; current primary controls guest Ansible tasks.
- **Preconditions:** Phase 8 passed; reviewed backup/PITR evidence; old primary remains fenced; explicit reseed approval.
- **Command:** Use tested quarantine procedure, configure only required replication access, run preflight and then rebuild below with sudo prompting.
- **PASS:** Authenticated replication check precedes deletion; rebuilt host is read-only and streaming with zero lag; new authenticated marker replicates.
- **Evidence:** Approvals, STOPPED and active firewall rules, full recap, slot/role checks and marker ID.
- **STOP if:** Any failed gate or partial rebuild: preserve state, diagnose, never repeat destructive reseed blindly.
- **Next:** Phase 10; keep quarantine and its replication exception.

Use the quarantine route already prepared and rehearsed in phase 5:
[PROXMOX-QUARANTINE.md](PROXMOX-QUARANTINE.md). Start the old VM with every
network link disconnected. Execute the labelled stop helper directly through
Guest Agent, require exited=1, exitcode=0 and STOPPED, then inspect applied
IPv4/IPv6 quarantine rules before reconnecting restricted SSH.
A helper installation alone is not proof that quarantine works.

Stopped services may be inactive or failed only with zero MainPID/ControlPID
and no running user containers; preserve failure evidence. Remove the old
inbound replication rule on .102. On current primary allow only .102 to reach
.108:5432. Establish verified key-based SSH from current primary to rebuild
host. Enable only the inspected Proxmox outbound replication exception.

Run read-only preflight:

```bash
cd "$HOME/todo-operations"
ansible-playbook --ask-become-pass --inventory ansible/inventory-recovery.ini \
  ansible/preflight-standby-rebuild.yml \
  --extra-vars \
  '{"todo_confirm_old_primary_fenced":"todo-primary is fenced","todo_confirm_reseed":"todo-primary"}'
```

Only after every assertion passes, run:

```bash
ansible-playbook --ask-become-pass --inventory ansible/inventory-recovery.ini \
  ansible/rebuild-standby.yml \
  --extra-vars \
  '{"todo_confirm_old_primary_fenced":"todo-primary is fenced","todo_confirm_reseed":"todo-primary"}'
```

Pass when authenticated `IDENTIFY_SYSTEM` precedes volume deletion, a fresh
base backup initializes `.102`, and final state is `streaming|async`.

Run `ansible/cluster-status.yml`, create an authenticated Todo through
`todo.test`, and verify it directly on rebuilt standby.

## 10. Final reboot sequence

- **Where:** Proxmox node Shell for one reboot at a time; current primary for cluster checks; client/build host for HTTPS.
- **Preconditions:** Phase 9 passed, both roles independently verified and streaming healthy.
- **Command:** Follow the numbered sequence below; verify standby fully before rebooting primary.
- **PASS:** Roles/data/CA/backup survive, application healthy, streaming zero lag and no failed units.
- **Evidence:** Each before/after boot ID, fresh cluster status, health, TLS/issuer and markers.
- **STOP if:** Standby not recovered, replication unhealthy or any data/TLS failure. Do not reboot the other host.
- **Next:** Phase 11.

1. Reboot only rebuilt standby.
2. Require `t|on`, database-only services and resumed streaming.
3. Run `cluster-status.yml`.
4. Reboot only current primary.
5. Require all application services, `nginx -t`, `f|off|on|1h`, persistent
   backup, unchanged TLS CA and application readiness.
6. Run `cluster-status.yml` again.
7. Verify trusted HTTPS, stable issuer and all markers from the client.
8. Record backup/WAL size and free disk.

Pass only when final status reports writable primary, healthy archiving,
`streaming|async`, active usable slot, zero measured lag, read-only recovery
standby, and healthy application through trusted HTTPS.

## 11. Final Kube-runtime gate

- **Where:** Client/build host collects evidence; both guests supply fresh checks.
- **Preconditions:** All previous phases passed on the recorded revision; any repair recorded.
- **Command:** Review the checks below and the private phase record; do not repeat an already verified reboot just because a new agent resumed.
- **PASS:** CLEAN PASS only for complete unchanged-revision evidence; otherwise record repaired functional pass or incomplete status.
- **Evidence:** Final role/topology record, phase outputs, real browser results and exact verdict with deviations.
- **STOP if:** Missing evidence, skipped authenticated tests, unresolved failure or revision drift; do not mark accepted.
- **Next:** Hand off the working topology. No automatic reset, retirement or state-file editing.

There is no migration stage in the normal DR path. Clean deployment,
standby bootstrap, promotion, backup and rebuild must already use these
workload services and pods:

```text
todo-app.service       todo-app pod
todo-keycloak.service  todo-keycloak pod
todo-postgres.service  todo-postgres pod
```

Run `python3 /opt/todo/bin/todo_dr_run.py ... verify` after a runner-completed
rebuild. Always run `ansible/cluster-status.yml` directly for fresh evidence:
the runner skips verification already marked completed. If rebuild needed a
manual tail repair, preserve its failed runner state and use direct cluster
verification; follow the [repair and handoff rules](MANUAL-DR-QUICKSTART.md).
That run is a repaired functional pass, not a clean pass. Require
schema migrations applied by the init container, healthy backend/frontend,
nginx-to-backend loopback traffic, shared-service DNS through `todo.network`,
unchanged PostgreSQL identity, persistent data, streaming replication and WAL
archive health.

Use the sequential reboot evidence from phase 10; do not add another reboot.
After those boots, require `NRestarts=0` for `todo-app.service`, no failed user
units, readiness, stable issuer and trusted browser E2E. The legacy migration
and rollback playbooks remain transition evidence only until this complete
acceptance gate permits their retirement.

## Historical evidence — not instructions or current acceptance

The following result belongs to its recorded historical revision and does not
approve the current grouped runtime or replace any phase card.

### Verified clean nginx drill - 2026-09-01

The full procedure passed from clean Oracle Linux 9.8 snapshots using source
revision `d8506f75721f92b704eec0669bf2dda36ff18bdb`.

- Both packages reported that exact clean revision.
- Initial nginx deployment passed trusted HTTPS, two Playwright tests, reboot
  and idempotent reinstall.
- Promotion completed with zero local apply lag and produced `f|off`.
- Stable issuer, authenticated failover write and reboot passed.
- Base backup `base-20260901T160954Z` was 52 MiB and verified.
- Isolated PITR contained only the before-row; live data retained both rows.
- Rebuild produced `todo_rebuilt_standby|streaming|async|0`.
- Final primary reported `f|off|on|1h`, zero archive failures and healthy apps.
- Final standby reported `t|on` with receive/replay LSN `0/C0025E8`.
- Backup use was 52 MiB base plus 161 MiB WAL, with 16 GiB free.
- nginx CA SHA-256 remained
  `a9b4ec01d39da1e5d1ef698308faf357655444867013c28c702da01a3f8a9e13`.
