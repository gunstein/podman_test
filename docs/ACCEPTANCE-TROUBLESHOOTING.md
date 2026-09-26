# Acceptance troubleshooting

Use [ACCEPTANCE.md](ACCEPTANCE.md) for normal execution. This reference is for
failed gates and interrupted runs. Preserve failure evidence, keep fencing, and
obtain explicit approval before destructive recovery. A repaired functional pass
does not satisfy unchanged-revision acceptance. Never weaken SELinux, fapolicyd,
firewalld or SSH/TLS verification to continue.

## Symptoms and safe next observations

| Observation | Safe next action |
|---|---|
| `qm guest exec` returns only a PID | Keep links disconnected; poll `qm guest exec-status VMID PID`. Require `exited=1`, `exitcode=0` and `STOPPED`; the outer `qm` exit code is insufficient. |
| `pve-firewall status` says pending changes | Wait and inspect again. Verify actual IPv4 AND IPv6 rules before reconnecting under quarantine. |
| PostgreSQL failed on disconnected DHCP boot | Inspect the journal for `bind: cannot assign requested address`. The helper accepts inactive/failed only after stop, zero service PIDs and no running containers; it preserves the failure warning. Do not start the old database after promotion. |
| Helper reports hostname missing after an update | Inspect `ls -lZ` through Guest Agent. Required label: `virt_qemu_ga_unconfined_exec_t`. The installer restores existing persistent policy after replacement. Do not disable SELinux or broadly enable Guest Agent commands. |
| Direct Guest Agent diagnostic is denied | Do not keep guessing privileged commands. Use reviewed restricted SSH only after stop/process/container and active firewall evidence is available. Read `journalctl -b _SYSTEMD_USER_UNIT=todo-postgres.service` (likewise `notes-postgres.service` and `keycloak-postgres.service`); `--user` may see no journal here. |
| fapolicyd trust step retries then succeeds | Normal asynchronous refresh; judge the final result. If exhausted, inspect exact path/size/hash trust, never trust whole directories or disable fapolicyd. |
| A `podman healthcheck run` unit for Keycloak shows failed right after boot | Keycloak starts slower than its first health checks. Wait a minute and look again: a unit that clears itself is expected. One that stays failed, or a Keycloak container that is not healthy, is a real failure. |
| `/ready` works but login is still 503 after boot | Wait for Keycloak/discovery and browser tests too. `/ready` alone is not whole-application acceptance. |
| Standby logs `Connection timed out` to the primary after a quarantine rehearsal, while SSH to the primary works | The Proxmox VM firewall is probably still enabled: its quarantine profile drops everything but SSH. Read the VM's `firewall/options`; the rehearsal's own restore step sets `enable=0` and reboots. Never work around it inside the guest. |
| Standby gets `Connection refused` from the primary, or the primary's `ss -ltn` shows 5432-5434 only on `127.0.0.1` | The LAN publication of the replication ports is gone, typically because the installer was run again after replication was set up. Do not rerun anything; STOP and record the database `.kube` files' `PublishPort` lines. Restoring publication is an operator decision. |
| After a standby reboot, receive LSN is lower than replay LSN (for example `0/3000000` and `0/3000060`) on an idle database | Expected: the walreceiver restarts at the start of the WAL segment, and receive catches up with the next WAL. Nothing is left to replay; `app_dr.py status` shows 0 bytes and a note. Older revisions printed a negative lag and refused promotion: use a revision with the fix instead of working around it. |
| Replication is absent immediately after reconnect | Check receiver logs and bounded reconnect progress. A prior TCP attempt can still be timing out. Require streaming and zero lag before the next gate; do not restart or reseed blindly. |
| Rebuild fails at any point | STOP. Record the failed task name and exact error. Inspect both roles, slot, volumes and streaming before deciding what remains. Never repeat the destructive command. |

### Specific recovery: rebuild succeeded, only DR-tool installation failed

This applies ONLY to a failure in `rebuild-standby`'s last steps (installing
`app_dr.py` on the rebuilt standby, for example because `sudo -n` stopped
working) after base backup and recovery startup. First independently verify all three databases
`.108` writable, `.102` read-only, `todo_rebuilt_standby`,
`notes_rebuilt_standby` and `keycloak_rebuilt_standby` streaming, and
quarantine intact.
Then the operator may finish the non-destructive tail on the promoted host,
with a small inventory that names the promoted host as primary and the rebuilt
host as standby:

```bash
cd "$HOME/todo-operations"
cat > dr-tool.yaml <<'EOF'
user: gunstein
hosts:
  todo-standby: {role: primary, address: 192.168.0.108, local: true}
  todo-primary: {role: standby, address: 192.168.0.102}
EOF
export PYTHONPATH="$PWD/deploy/ops" PYTHONDONTWRITEBYTECODE=1
python3 -m app_ops --inventory dr-tool.yaml install-dr-tool
python3 -m app_ops --inventory recovery.yaml cluster-status
```

This is an exceptional repair, NOT the normal rebuild command. If the failure
boundary differs, stop and inspect the current code. Preserve
the original rebuild failure; there is no supported reconciliation command.
Do not edit state to completed or rerun rebuild to turn it green. Record
manual completion and direct verification in the run log.

## Rootless Podman lock state after cloning

If Podman reports `Refreshing volume .* acquiring lock .* file exists`, inspect
for inherited lock allocation metadata. After stopping all user Podman services
and processes, `podman system renumber` can repair lock allocation. It is not a
reason to delete database volumes or to start the old writable database.

## Console fallback when Guest Agent preparation cannot be completed

Keep every old-primary network link disconnected. A separately reviewed console
session may run the same root-owned `app-quarantine.sh stop EXPECTED_HOST USER`
as root. Require its successful STOPPED result and inspect applied IPv4/IPv6
quarantine rules before reconnecting restricted SSH. Do not improvise an SSH
exception to obtain the stop evidence. If these requirements cannot be met,
remain fenced and stop the drill.

## Obsolete client CA from an earlier drill

Inspect the system and Chromium trust stores and compare fingerprints. Remove
only the exact obsolete lab certificate after identifying it; never delete a
certificate merely because its filename resembles an old lab certificate. Import only
the verified public CA for the current serving host. See [TLS](TLS.md).

## Existing disposable PITR resources

Inspect `<app>-postgres-restore` and `<app>-postgres-restore-data` (for example
`todo-postgres-restore`), the selected
backup and the failed restore logs. Preserve the live and backup volumes.
After explicit approval to discard only the disposable resources, repeat the
reviewed `app_backup.py --app APP restore --backup NAME --target POINT` command with
`--replace`. This option is not permission to modify live data. Recheck network
`none`, paused read-only recovery, before/after comparison and live data before
approving cleanup. Record the original failure and repair in the private run log.
