#!/bin/sh
set -eu

failed=0

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: missing command: $1" >&2
        failed=1
    fi
}

for command in podman systemctl tar sha256sum df awk; do
    require_command "$command"
done

if [ "$failed" -ne 0 ]; then
    exit 1
fi

required_python=
if [ -f ./PYTHON_VERSION ]; then
    required_python=$(cat ./PYTHON_VERSION)
fi

python_command=
for candidate in "python${required_python}" python3.12 python3; do
    if [ "$candidate" = python ]; then
        continue
    fi
    if command -v "$candidate" >/dev/null 2>&1 &&
        REQUIRED_PYTHON="$required_python" "$candidate" -c '
import os
import sys

required = os.environ.get("REQUIRED_PYTHON")
current = f"{sys.version_info.major}.{sys.version_info.minor}"
raise SystemExit(
    sys.version_info < (3, 10) or bool(required and current != required)
)
'
    then
        python_command=$candidate
        break
    fi
done
if [ -z "$python_command" ]; then
    if [ -n "$required_python" ]; then
        echo "ERROR: this bundle requires Python $required_python." >&2
    else
        echo "ERROR: Python 3.10 or newer is required." >&2
    fi
    exit 1
fi
echo "INFO: using $($python_command --version 2>&1)"

if systemctl is-active --quiet fapolicyd 2>/dev/null; then
    require_command sudo
    require_command fapolicyd-cli
    echo "INFO: active fapolicyd detected; installer will register the bundled Ansible runtime as trusted."
fi

if [ -f ./PYTHON_VERSION ] && [ ! -d ./ansible-runtime/ansible ]; then
    echo "ERROR: bundled Ansible runtime is missing." >&2
    failed=1
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

if [ "$existing_deployment" = false ] && ! "$python_command" - <<'PY'
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
