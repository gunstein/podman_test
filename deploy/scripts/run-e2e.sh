#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python="$project_root/todo-backend/.venv/bin/python"

if [[ ! -x "$python" ]]; then
  echo "Create todo-backend/.venv and install todo-backend/requirements-e2e.txt first." >&2
  exit 1
fi

if ! podman container exists keycloak; then
  echo "The keycloak container is not running. Deploy the application first." >&2
  exit 1
fi

read -rsp "E2E password for testuser: " E2E_PASSWORD
echo
read -rsp "Current Keycloak admin password: " KEYCLOAK_ADMIN_PASSWORD
echo

ca_file=$(mktemp)

export E2E_PASSWORD
export E2E_USERNAME=testuser
export KEYCLOAK_ADMIN_PASSWORD

cleanup() {
  unset E2E_PASSWORD E2E_USERNAME KEYCLOAK_ADMIN_PASSWORD
  rm -f "$ca_file"
}
trap cleanup EXIT

"$python" "$project_root/e2e/provision_user.py"

# test_multi_app.py (Todo/Notes SSO) never ignores TLS errors, even here; it
# needs the real demo CA, exported fresh so a rebuilt CA is picked up too.
podman exec nginx cat /var/lib/todo-tls/ca.crt > "$ca_file"

E2E_BASE_URL=https://localhost:8443 E2E_NOTES_URL=https://notes.test:8443 \
  E2E_MULTI_APP=1 E2E_CA_FILE="$ca_file" E2E_IGNORE_HTTPS_ERRORS=true \
  "$python" -m pytest "$project_root/e2e" --browser chromium
