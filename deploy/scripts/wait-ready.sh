#!/bin/bash
# Wait until a host's workloads are really up, not only started, after a boot
# or an install. A systemd unit is active as soon as its pod starts; the
# containers, health checks and HTTP answers follow a few seconds later.
# Usage: wait-ready.sh app|standby PODS CONTAINERS [HOSTNAME...]
#   PODS and CONTAINERS are space-separated names; apps.Platform.ready(role)
#   gives them, from the installation's platform (callers pass them, so this
#   script keeps no list of its own).
#   app:     the pods' services active, the containers running and healthy, and
#            /ready answering on 127.0.0.1:8080 for each HOSTNAME given (each
#            app's public hostname).
#   standby: the same for the databases; no HOSTNAME.
# Prints what it still waits for every 10 seconds, READY when done, and exits 1
# after WAIT_TIMEOUT seconds (default 300). It only reads; it changes nothing.
# Runs on the host itself, also over SSH: ssh HOST 'bash -s' -- app PODS CONTAINERS < wait-ready.sh
set -u
mode=${1:-}
if [ $# -lt 3 ] || { [ "$mode" != app ] && [ "$mode" != standby ]; }; then
  echo "usage: $0 app|standby PODS CONTAINERS [HOSTNAME...]" >&2
  exit 2
fi
services=$2 containers=$3
shift 3
hostnames=$*

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
echo "READY: $mode on $(hostname) after ${SECONDS}s"
