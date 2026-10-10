#!/bin/sh
# Read-only host checks before an offline install (install.sh runs it):
# commands, rootless Podman, Quadlet, user namespaces, the user systemd
# manager, free ports, disk and memory. Changes nothing.
set -eu

failed=0

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: missing command: $1" >&2
        failed=1
    fi
}

for command in podman systemctl python3 tar sha256sum df awk; do
    require_command "$command"
done

if [ "$failed" -ne 0 ]; then
    exit 1
fi

# The bundle's Kube YAML and units are rendered at build time; this install
# needs only the Python standard library (no Jinja2 or PyYAML).

if systemctl is-active --quiet fapolicyd 2>/dev/null; then
    echo "INFO: active fapolicyd: trust the verified installer Python files before running install.sh."
    echo "See deploy/offline/FAPOLICYD.md for exact-file trust."
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

allowed_ports=""
for container_ports in \
    "todo-postgres:5432" \
    "notes-postgres:5433" \
    "keycloak-postgres:5434" \
    "nginx:8080,8443"
do
    container=${container_ports%%:*}
    ports=${container_ports#*:}
    if podman container exists "$container" && \
        [ "$(podman inspect --format '{{.State.Running}}' "$container")" = true ]; then
        allowed_ports="${allowed_ports}${allowed_ports:+,}${ports}"
    fi
done

if ! PLATFORM_ALLOWED_PORTS="$allowed_ports" python3 - <<'PY'
import os
import socket
import subprocess
import sys

allowed = {
    int(port)
    for port in os.environ.get("PLATFORM_ALLOWED_PORTS", "").split(",")
    if port
}
failed = []
# Backend port 8000 is pod-local, not a published host port.
for port in (5432, 5433, 5434, 8080, 8443):
    sock = socket.socket()
    try:
        # Only a listener counts: SO_REUSEADDR lets the bind pass over connections
        # in TIME_WAIT, which an install's own checks leave for a minute after an
        # uninstall; nginx and Podman bind with it too.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
    except OSError:
        if port not in allowed:
            failed.append(port)
    finally:
        sock.close()

if failed:
    # What listens there, as far as this user may see (ss shows other users' sockets without the process).
    for port in failed:
        try:
            listening = subprocess.run(["ss", "-Hltnp", "sport = :%d" % port], capture_output=True,
                                       text=True, check=False).stdout.strip()
        except OSError:
            listening = ""
        print("INFO: port %d: %s" % (port, listening or "ss shows no listener now"), file=sys.stderr)
    raise SystemExit(
        "ERROR: localhost ports already in use by an unexpected process: "
        + ", ".join(map(str, failed))
    )
PY
then
    failed=1
fi

# Token expiry, TLS validity and log times need a right clock: a time service
# such as chronyd, synchronised. A warning, since the install itself works.
if command -v timedatectl >/dev/null 2>&1 && \
    [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" != yes ]; then
    echo "WARNING: the clock is not synchronised (timedatectl). Turn on a time service," >&2
    echo "for example: sudo systemctl enable --now chronyd; then check chronyc tracking." >&2
fi

graph_root=$(podman info --format '{{.Store.GraphRoot}}')
available_kib=$(df -Pk "$graph_root" | awk 'NR == 2 {print $4}')
memory_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
echo "INFO: Podman GraphRoot: $graph_root"
echo "INFO: free disk: $((available_kib / 1024)) MiB"
echo "INFO: total memory: $((memory_kib / 1024)) MiB"
echo "INFO: recommended minimum: 10240 MiB free disk and 4096 MiB memory."

if [ "$failed" -ne 0 ]; then
    exit 1
fi

echo "Preflight checks passed."
