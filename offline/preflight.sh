#!/bin/sh
set -eu

failed=0

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: missing command: $1" >&2
        failed=1
    fi
}

for command in podman systemctl ansible-playbook python3 tar sha256sum df awk; do
    require_command "$command"
done

if [ "$failed" -ne 0 ]; then
    exit 1
fi

if systemctl is-active --quiet fapolicyd 2>/dev/null; then
    echo "INFO: active fapolicyd detected; using RPM-managed Ansible and Python."
fi

ansible_version=$(ansible-playbook --version | awk 'NR == 1 {gsub(/[^0-9.]/, "", $3); print $3}')
ansible_major=$(printf '%s' "$ansible_version" | awk -F. '{print $1}')
ansible_minor=$(printf '%s' "$ansible_version" | awk -F. '{print $2}')
if [ -z "$ansible_major" ] || [ "$ansible_major" -lt 2 ] || \
    { [ "$ansible_major" -eq 2 ] && [ "$ansible_minor" -lt 14 ]; }; then
    echo "ERROR: ansible-core 2.14 or newer is required." >&2
    failed=1
else
    echo "INFO: using ansible-core $ansible_version"
fi

if ! rootless=$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null); then
    echo "ERROR: Podman cannot inspect the current user's rootless runtime." >&2
    failed=1
elif [ "$rootless" != "true" ]; then
    echo "ERROR: Podman is not running rootless for the current user." >&2
    failed=1
fi

quadlet_found=false
for path in \
    /usr/libexec/podman/quadlet \
    /usr/lib/systemd/system-generators/podman-system-generator \
    /usr/lib/systemd/user-generators/podman-user-generator \
    /usr/local/lib/systemd/system-generators/podman-system-generator \
    /usr/local/lib/systemd/user-generators/podman-user-generator
do
    if [ -x "$path" ]; then
        quadlet_found=true
        break
    fi
done
if [ "$quadlet_found" = false ]; then
    echo "ERROR: Podman's Quadlet systemd generator was not found." >&2
    failed=1
fi

if ! podman unshare true >/dev/null 2>&1; then
    echo "ERROR: rootless user namespaces are not configured." >&2
    echo "Check /etc/subuid and /etc/subgid for the current user." >&2
    failed=1
fi

if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "ERROR: the current user has no working systemd user manager." >&2
    failed=1
fi

existing_deployment=false
if podman container exists todo-postgres || podman container exists todo-frontend; then
    existing_deployment=true
    echo "INFO: existing Todo containers found; skipping clean-target port checks."
fi

if [ "$existing_deployment" = false ] && ! python3 - <<'PY'
import socket

failed = []
for port in (5432, 8000, 8080, 8443):
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        failed.append(port)
    finally:
        sock.close()

if failed:
    raise SystemExit("ERROR: localhost ports already in use: " + ", ".join(map(str, failed)))
PY
then
    failed=1
fi

available_kib=$(df -Pk . | awk 'NR == 2 {print $4}')
memory_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
echo "INFO: free disk: $((available_kib / 1024)) MiB"
echo "INFO: total memory: $((memory_kib / 1024)) MiB"
echo "INFO: recommended minimum: 10240 MiB free disk and 4096 MiB memory."

if [ "$failed" -ne 0 ]; then
    exit 1
fi

echo "Preflight checks passed."
