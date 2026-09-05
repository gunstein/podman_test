#!/bin/sh
# Run via Guest Agent with every VM network link disconnected.
# This stops workloads; it does not fence the VM or delete database data.
set -eu
if [ "$#" -ne 3 ]; then
  echo "Usage: bash todo-quarantine.sh check|stop EXPECTED_HOST SERVICE_USER" >&2
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
for unit in todo-app.service todo-keycloak.service todo-postgres.service; do
  test "$(as_user systemctl --user show "$unit" --property=LoadState --value)" = loaded
done
if [ "$action" = check ]; then
  as_user podman ps --format '{{.Names}}'
  printf 'READY: host=%s user=%s; no services changed\n' "$expected_host" "$service_user"
  exit 0
fi
as_user systemctl --user stop todo-app.service todo-keycloak.service todo-postgres.service
for unit in todo-app.service todo-keycloak.service todo-postgres.service; do
  test "$(as_user systemctl --user show "$unit" --property=ActiveState --value)" = inactive
done
test -z "$(as_user podman ps --format '{{.Names}}')" || {
  echo "Containers still running; keep every VM network link disconnected" >&2
  exit 1
}
printf 'STOPPED: host=%s; Todo services inactive; no running user containers\n' "$expected_host"
echo 'Keep hypervisor quarantine in place. This does not authorize rebuild or promotion.'
