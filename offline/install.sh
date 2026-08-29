#!/bin/sh
set -eu

bundle_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

cd "$bundle_directory"
sha256sum --check SHA256SUMS
sh "$bundle_directory/preflight.sh"

extra_vars=$(python3 - "$bundle_directory" <<'PY'
import json
import sys

print(json.dumps({"deployment_mode": "offline", "bundle_directory": sys.argv[1]}))
PY
)

ANSIBLE_PIPELINING=true ansible-playbook \
  --inventory "$bundle_directory/ansible/inventory.ini" \
  "$bundle_directory/ansible/deploy.yml" \
  --extra-vars "$extra_vars"
