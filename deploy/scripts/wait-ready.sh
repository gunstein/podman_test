#!/bin/bash
# Wait until a host's workloads are really up, not only started, after a boot
# or an install. A systemd unit is active as soon as its pod starts; the
# containers, health checks and HTTP answers follow a few seconds later.
# Usage: wait-ready.sh app|standby
#   app:     all seven services, their containers running and healthy, and
#            /ready answering for todo.test and notes.test on 127.0.0.1:8080.
#   standby: the three PostgreSQL services and containers only.
# Prints what it still waits for every 10 seconds, READY when done, and exits 1
# after WAIT_TIMEOUT seconds (default 300). It only reads; it changes nothing.
# Runs on the host itself, also over SSH: ssh HOST 'bash -s' -- app < wait-ready.sh
set -u
databases="todo-postgres notes-postgres keycloak-postgres"
case ${1:-} in
  app)
    services="shared-proxy todo-app notes-app keycloak $databases"
    containers="nginx todo-backend todo-frontend notes-backend notes-frontend keycloak $databases"
    hostnames="todo.test notes.test" ;;
  standby)
    services=$databases containers=$databases hostnames="" ;;
  *)
    echo "usage: $0 app|standby" >&2
    exit 2 ;;
esac

# Print the first thing that is not ready yet, or nothing.
missing() {
  for service in $services; do
    systemctl --user is-active --quiet "$service.service" || { echo "service $service"; return; }
  done
  for container in $containers; do
    state=$(podman container inspect --format \
      '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
      "$container" 2>/dev/null) || { echo "container $container"; return; }
    case $state in
      "true " | "true healthy") ;;
      *) echo "container $container ($state)"; return ;;
    esac
  done
  for hostname in $hostnames; do
    curl --silent --fail --max-time 5 -H "Host: $hostname" http://127.0.0.1:8080/ready >/dev/null ||
      { echo "readiness of $hostname"; return; }
  done
}

deadline=$((SECONDS + ${WAIT_TIMEOUT:-300}))
last_report=0
while waiting=$(missing) && [ -n "$waiting" ]; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "NOT READY after ${WAIT_TIMEOUT:-300}s: $waiting"
    exit 1
  fi
  if [ $((SECONDS - last_report)) -ge 10 ]; then
    echo "waiting for $waiting"
    last_report=$SECONDS
  fi
  sleep 2
done
echo "READY: $1 on $(hostname) after ${SECONDS}s"
