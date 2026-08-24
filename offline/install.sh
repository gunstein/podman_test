#!/bin/sh
set -eu

bundle_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fapolicyd_active=false
trust_file=todo-offline

trust_directory() {
    directory=$1
    sudo fapolicyd-cli --file delete "$directory/" \
        --trust-file "$trust_file" >/dev/null 2>&1 || true
    sudo fapolicyd-cli --file add "$directory/" --trust-file "$trust_file"
    sudo fapolicyd-cli --update
}

remove_trust_directory() {
    directory=$1
    sudo fapolicyd-cli --file delete "$directory/" \
        --trust-file "$trust_file" >/dev/null 2>&1 || true
    sudo fapolicyd-cli --update
}

cd "$bundle_directory"
if systemctl is-active --quiet fapolicyd 2>/dev/null; then
    fapolicyd_active=true
    echo "Active fapolicyd detected; administrator approval is required."
    trust_directory "$bundle_directory/ansible-runtime"
fi

if ! sha256sum --check SHA256SUMS; then
    if [ "$fapolicyd_active" = true ]; then
        remove_trust_directory "$bundle_directory/ansible-runtime"
    fi
    echo "ERROR: bundle integrity verification failed." >&2
    exit 1
fi
sh "$bundle_directory/preflight.sh"

required_python=$(cat "$bundle_directory/PYTHON_VERSION")
python_command=
for candidate in "python${required_python}" python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        REQUIRED_PYTHON="$required_python" "$candidate" -c '
import os
import sys

required = os.environ["REQUIRED_PYTHON"]
current = f"{sys.version_info.major}.{sys.version_info.minor}"
raise SystemExit(current != required)
'
    then
        python_command=$candidate
        break
    fi
done
if [ -z "$python_command" ]; then
    echo "ERROR: this bundle requires Python $required_python." >&2
    exit 1
fi

ANSIBLE_PIPELINING=true PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$bundle_directory/ansible-runtime" \
  "$python_command" -m ansible.cli.playbook \
  --inventory "$bundle_directory/ansible/inventory.ini" \
  "$bundle_directory/ansible/deploy.yml" \
  --extra-vars "deployment_mode=offline bundle_directory=$bundle_directory"
