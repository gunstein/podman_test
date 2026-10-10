#!/usr/bin/env bash
# Browser end-to-end tests (Playwright) against a running stack: creates the
# test user in Keycloak, then logs in to Todo and Notes.
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
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
podman exec nginx cat /var/lib/platform-tls/ca.crt > "$ca_file"

# Each app's origin on the development stack: platform.yaml's local environment.
origin() {
  local platform=(env PYTHONPATH="$project_root/deploy/installer" python3 -m app_installer platform)
  echo "https://$("${platform[@]}" hostname "$1" --environment local):$("${platform[@]}" public-port --environment local)"
}

E2E_BASE_URL=$(origin todo) E2E_NOTES_URL=$(origin notes) \
  E2E_MULTI_APP=1 E2E_CA_FILE="$ca_file" E2E_IGNORE_HTTPS_ERRORS=true \
  "$python" -m pytest "$project_root/e2e" --browser chromium
