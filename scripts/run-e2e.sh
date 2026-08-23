#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python="$project_root/backend/.venv/bin/python"

if [[ ! -x "$python" ]]; then
  echo "Create backend/.venv and install backend/requirements-e2e.txt first." >&2
  exit 1
fi

if ! podman container exists todo-keycloak; then
  echo "The todo-keycloak container is not running. Deploy the application first." >&2
  exit 1
fi

read -rsp "E2E password for testuser: " E2E_PASSWORD
echo
read -rsp "Current Keycloak admin password: " KEYCLOAK_ADMIN_PASSWORD
echo

export E2E_PASSWORD
export E2E_USERNAME=testuser
export KEYCLOAK_ADMIN_PASSWORD

cleanup() {
  unset E2E_PASSWORD E2E_USERNAME KEYCLOAK_ADMIN_PASSWORD
}
trap cleanup EXIT

"$python" "$project_root/e2e/provision_user.py"

E2E_BASE_URL=https://localhost:8443 E2E_IGNORE_HTTPS_ERRORS=true "$python" -m pytest "$project_root/e2e" --browser chromium
