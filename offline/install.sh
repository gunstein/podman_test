#!/bin/sh
set -eu

bundle_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

cd "$bundle_directory"
sha256sum --check SHA256SUMS

python3 -m venv "$bundle_directory/.venv"
"$bundle_directory/.venv/bin/python" -m pip install \
  --no-index \
  --find-links "$bundle_directory/wheels" \
  --requirement "$bundle_directory/ansible/requirements.txt"

"$bundle_directory/.venv/bin/ansible-playbook" \
  --inventory "$bundle_directory/ansible/inventory.ini" \
  "$bundle_directory/ansible/deploy.yml" \
  --extra-vars "deployment_mode=offline bundle_directory=$bundle_directory"
