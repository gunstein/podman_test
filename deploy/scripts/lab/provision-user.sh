#!/usr/bin/env bash
# Create the acceptance test user in Keycloak on the serving host
# (docs/ACCEPTANCE-AGENT.md C9.4). e2e/provision_user.py talks to
# http://127.0.0.1:8080, so this forwards that port through one SSH tunnel and
# closes exactly that tunnel afterwards. The Keycloak admin password and the
# testuser password stay in this process's environment, never in a file.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 user@serving-host" >&2
  exit 1
fi

ssh_target=$1
state="$XDG_RUNTIME_DIR/todo-acceptance"
tunnel="$state/tunnel"

ssh -o ExitOnForwardFailure=yes -o ControlMaster=yes -o ControlPath="$tunnel" \
  -f -N -L 127.0.0.1:8080:127.0.0.1:8080 "$ssh_target"
trap 'ssh -o ControlPath="$tunnel" -O exit "$ssh_target"' EXIT

KEYCLOAK_ADMIN_PASSWORD="$(ssh -o BatchMode=yes "$ssh_target" \
  "podman secret inspect --showsecret --format '{{.SecretData}}' keycloak-admin-password")"
E2E_PASSWORD="$(cat "$state/e2e-password")"
export KEYCLOAK_ADMIN_PASSWORD E2E_PASSWORD
todo-backend/.venv/bin/python e2e/provision_user.py
