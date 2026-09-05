# Full lab acceptance

This is the canonical destructive acceptance test for the three-workload
Podman Kube implementation. Individual runbooks explain each operation; this
document defines order, evidence and pass criteria.

The procedure permanently destroys the old primary database during the final
rebuild. Run it only on disposable lab hosts with infrastructure fencing.

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

## 1. Clean-host evidence

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
Use `curl` without `-k` for the system-trust assertion. The project's
`run-e2e.sh` deliberately sets `E2E_IGNORE_HTTPS_ERRORS=true` because the
Playwright Chromium build may not consume the Ubuntu system CA store; that
browser setting is not a substitute for the separate trusted `curl` check.

Reboot the VM. Verify services, marker data and TLS CA persistence, then rerun
`sh ./install.sh --publish-address 192.168.0.102`. Pass when the second
deployment reports `changed=0`.

## 4. Initial standby bootstrap

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
fingerprint from its console, then follow `ansible/STANDBY-ARCHITECTURE.md` to
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

## 6. Fence and promote

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

Boot old primary with workload traffic blocked. Stop all Todo services before
permitting management SSH:

```bash
systemctl --user stop todo-app.service todo-keycloak.service todo-postgres.service
systemctl --user is-active todo-app.service todo-keycloak.service todo-postgres.service || true
podman ps
```

All services must be inactive and no container may run. Remove the old inbound
replication rule on `.102`. On current primary, allow only `.102` to reach
`.108:5432`. Establish key-based SSH from current primary to rebuild host.

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

There is no migration stage in the normal DR path. Clean deployment,
standby bootstrap, promotion, backup and rebuild must already use these
workload services and pods:

```text
todo-app.service       todo-app pod
todo-keycloak.service  todo-keycloak pod
todo-postgres.service  todo-postgres pod
```

Run `python3 /opt/todo/bin/todo_dr_run.py ... verify` after rebuild. Require
schema migrations applied by the init container, healthy backend/frontend,
nginx-to-backend loopback traffic, shared-service DNS through `todo.network`,
unchanged PostgreSQL identity, persistent data, streaming replication and WAL
archive health.

Reboot only the current primary while the rebuilt standby remains available.
After boot, require `NRestarts=0` for `todo-app.service`, no failed user
units, readiness, stable issuer and trusted browser E2E. The legacy migration
and rollback playbooks remain transition evidence only until this complete
acceptance gate permits their retirement.

## Verified clean nginx drill - 2026-09-01

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
