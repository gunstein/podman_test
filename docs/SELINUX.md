# Podman and SELinux on Oracle Linux

This demo keeps SELinux enforcing. Container access depends on four independent
layers: Unix ownership and mode bits, rootless UID/GID mapping, SELinux labels,
and—on hardened hosts—fapolicyd trust. Making one layer permissive does not fix
the others.

## The mount suffixes used by the demo

| Suffix | What it changes | Use in this demo |
|---|---|---|
| `:Z` | Gives content a private container SELinux label | PostgreSQL data and private bind-mounted scripts |
| `:z` | Gives content a shared container SELinux label | Backup data read by both live PostgreSQL and disposable PITR |
| `:U` | Recursively changes ownership for the container UID/GID mapping | Initializing a new rootless PostgreSQL volume |

`:Z` and `:z` solve labeling. `:U` solves ownership. They are not substitutes.
`:U` mutates ownership in storage and can be expensive on a large tree, so the
demo uses it only where ownership initialization is intentional.

Rootless PostgreSQL runs as UID 999 inside the container. That identity normally
maps to a subordinate host UID rather than host UID 999. Inspect the mapping with:

```bash
podman unshare cat /proc/self/uid_map
podman unshare stat -c "%u:%g %a %n" \
  "$HOME/.local/share/containers/storage/volumes/todo-postgres-data/_data"
```

Changing a host path to mode `0777` is not a valid SELinux fix. Correct Unix
permissions can still receive an AVC denial, and a correctly labeled path can
still fail because of ownership.

## Troubleshooting sequence

1. Confirm the service and container failure:

   ```bash
   systemctl --user --no-pager --full status todo-postgres.service
   journalctl --user --unit todo-postgres.service --since "15 minutes ago"
   podman ps --all
   ```

2. Check enforcement, labels, permissions and rootless mapping:

   ```bash
   getenforce
   ls -Zd "$HOME/.config/todo"
   podman volume inspect todo-postgres-data
   podman unshare cat /proc/self/uid_map
   ```

3. Look for SELinux AVC records:

   ```bash
   sudo ausearch -m AVC -ts recent
   ```

4. If there is no AVC, look for fapolicyd FANOTIFY denials:

   ```bash
   sudo ausearch -m FANOTIFY -ts recent
   sudo journalctl -u fapolicyd --since "15 minutes ago"
   ```

Use this interpretation:

```text
permission denied
    ├── AVC record                 → SELinux label or policy
    ├── FANOTIFY denial / resp=2   → fapolicyd trust or rule
    └── neither                    → Unix mode, ownership, UID mapping, or mount flags
```

Use `restorecon` for paths that should have the normal host policy label. Use
Podman mount suffixes for container-managed paths. Do not disable SELinux or
fapolicyd to make the demo pass; that removes the mechanism the demo is meant
to teach.

For fapolicyd source and deployed-file trust lifecycle, see
[`offline/FAPOLICYD.md`](../offline/FAPOLICYD.md).
