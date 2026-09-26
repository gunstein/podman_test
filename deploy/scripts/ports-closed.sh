#!/bin/bash
# Check that a fenced host accepts no connections.
# Usage: ports-closed.sh HOST PORT...
# Prints each port as open, timeout or closed with the error (for example
# Connection refused or No route to host), and exits 1 if any is open.
# Runs anywhere with bash, also over SSH: ssh HOST 'bash -s' -- ARGS < ports-closed.sh
set -u
if [ $# -lt 2 ]; then
  echo "usage: $0 HOST PORT..." >&2
  exit 2
fi
host=$1
shift
result=0
for port in "$@"; do
  error=$(timeout "${PORT_TIMEOUT:-5}" bash -c "</dev/tcp/$host/$port" 2>&1)
  case $? in
    0) echo "$host:$port open"; result=1 ;;
    124) echo "$host:$port timeout" ;;
    *) echo "$host:$port closed (${error##*: })" ;;
  esac
done
if [ "$result" -eq 0 ]; then echo "CLOSED: $host"; else echo "OPEN PORTS on $host"; fi
exit "$result"
