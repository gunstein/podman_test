#!/bin/sh
set -eu

bundle_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

publish_address=127.0.0.1
if [ "$#" -ne 0 ]; then
  if [ "$#" -ne 2 ] || [ "$1" != --publish-address ]; then
    echo "Usage: sh install.sh [--publish-address HOST_IPV4]" >&2
    exit 2
  fi
  publish_address=$2
fi

extra_vars=$(python3 - "$bundle_directory" "$publish_address" <<'PY'
import ipaddress
import json
import sys

try:
    address = ipaddress.IPv4Address(sys.argv[2])
except ipaddress.AddressValueError:
    sys.exit("Publish address must be a host IPv4 address")
if address.is_unspecified or address.is_multicast or address.is_reserved:
    sys.exit("Publish address must identify a host, not a wildcard or reserved address")
print(json.dumps({
    "deployment_mode": "offline",
    "bundle_directory": sys.argv[1],
    "todo_publish_address": str(address),
}))
PY
)

cd "$bundle_directory"
sha256sum --check SHA256SUMS
sh "$bundle_directory/preflight.sh"

ansible-playbook \
  --inventory "$bundle_directory/ansible/inventory.ini" \
  "$bundle_directory/ansible/deploy.yml" \
  --extra-vars "$extra_vars"
