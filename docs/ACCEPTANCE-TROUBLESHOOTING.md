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
| Direct Guest Agent diagnostic is denied | Do not keep guessing privileged commands. Use reviewed restricted SSH only after stop/process/container and active firewall evidence is available. Read `journalctl -b _SYSTEMD_USER_UNIT=todo-postgres.service`; `--user` may see no journal here. |
| fapolicyd trust task retries then succeeds | Normal asynchronous refresh; judge final recap. If exhausted, inspect exact path/size/hash trust, never trust whole directories or disable fapolicyd. |
| `/ready` works but login is still 503 after boot | Wait for Keycloak/discovery and browser tests too. `/ready` alone is not whole-application acceptance. |
| Replication is absent immediately after reconnect | Check receiver logs and bounded reconnect progress. A prior TCP attempt can still be timing out. Require streaming and zero lag before the next gate; do not restart or reseed blindly. |
| Rebuild fails at any point | STOP. Record the failed task name and exact error. Inspect both roles, slot, volumes and streaming before deciding what remains. Never repeat the destructive command. |

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
or failure boundary differs, stop and inspect the current playbook. Preserve
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
session may run the same root-owned `todo-quarantine.sh stop EXPECTED_HOST USER`
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

Inspect `todo-postgres-restore` and `todo-postgres-restore-data`, the selected
backup and the failed restore logs. Preserve the live and backup volumes.
After explicit approval to discard only the disposable resources, repeat the
reviewed `todo_backup.py restore --backup NAME --target POINT` command with
`--replace`. This option is not permission to modify live data. Recheck network
`none`, paused read-only recovery, before/after comparison and live data before
approving cleanup. Record the original failure and repair in the private run log.
