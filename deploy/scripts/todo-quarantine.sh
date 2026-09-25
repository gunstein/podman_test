#!/bin/sh
# Run via Guest Agent with every VM network link disconnected.
# This stops workloads; it does not fence the VM or delete database data.
set -eu
if [ "$#" -ne 3 ]; then
  echo "Usage: todo-quarantine.sh check|stop EXPECTED_HOST SERVICE_USER" >&2
  exit 2
fi
action=$1
expected_host=$2
service_user=$3
case "$action" in check|stop) ;; *) exit 2 ;; esac
case "$service_user" in ''|-*|*[!a-zA-Z0-9_-]*) exit 2 ;; esac
test "$(id -u)" = 0 || { echo "Run through Guest Agent as root" >&2; exit 1; }
test "$(hostname)" = "$expected_host" || { echo "Wrong guest hostname" >&2; exit 1; }
service_uid=$(id -u "$service_user")
test "$service_uid" -gt 0
as_user() {
  runuser -u "$service_user" -- env XDG_RUNTIME_DIR="/run/user/$service_uid" "$@"
}
# One trusted registry owns the complete group; never leave another app running.
units=$(PYTHONPATH=/opt/todo/lib PYTHONDONTWRITEBYTECODE=1 python3 -c \
  'from app_installer.apps import services; print("\n".join(services()))')
set -f
# Intentional splitting of the registry's newline-separated, validated unit names.
# shellcheck disable=SC2086
set -- $units
test "$#" -ge 4 || { echo "Incomplete application service registry" >&2; exit 1; }
for unit in "$@"; do
  case "$unit" in
    *[!a-zA-Z0-9_.@-]*|'') echo "Invalid service name" >&2; exit 1 ;;
    *.service) ;;
    *) echo "Invalid service suffix" >&2; exit 1 ;;
  esac
done
for unit in "$@"; do
  test "$(as_user systemctl --user show "$unit" --property=LoadState --value)" = loaded
done
if [ "$action" = check ]; then
  as_user podman ps --format '{{.Names}}'
  printf 'READY: host=%s user=%s; no services changed\n' "$expected_host" "$service_user"
  exit 0
fi
as_user systemctl --user stop "$@"
for unit in "$@"; do
  state=$(as_user systemctl --user show "$unit" --property=ActiveState --value)
  case "$state" in
    inactive) ;;
    failed) echo "WARNING: $unit remains failed; inspect its journal. Failure state preserved." >&2 ;;
    *) echo "Not stopped: $unit ActiveState=$state; keep quarantine in place" >&2; exit 1 ;;
  esac
  for property in MainPID ControlPID; do
    pid=$(as_user systemctl --user show "$unit" --property="$property" --value)
    test "$pid" = 0 || {
      echo "Not stopped: $unit $property=$pid; keep quarantine in place" >&2
      exit 1
    }
  done
done
remaining_containers=$(as_user podman ps --format '{{.Names}}')
test -z "$remaining_containers" || {
  echo "Containers still running; keep every VM network link disconnected" >&2
  exit 1
}
printf 'STOPPED: host=%s; Application services stopped (inactive or failed), no service processes; no running user containers\n' "$expected_host"
echo 'Keep hypervisor quarantine in place. This does not authorize rebuild or promotion.'
