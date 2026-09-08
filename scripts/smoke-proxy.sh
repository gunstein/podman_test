#!/usr/bin/env bash
# Validate the actual rendered proxy configuration and persistent TLS bootstrap.
set -euo pipefail
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work_directory=$(mktemp -d)
image="localhost/todo-proxy-smoke:$$"
volume="todo-proxy-smoke-$$"
cleanup() {
  podman volume rm --force "$volume" >/dev/null 2>&1 || true
  podman image rm "$image" >/dev/null 2>&1 || true
  rm -rf "$work_directory"
}
trap cleanup EXIT
"$project_root/scripts/render-kube-runtime.sh" "$project_root/helm/todo/values-prod.yaml" "$work_directory"
python3 - "$work_directory" <<'PY'
import sys
from pathlib import Path
import yaml
root = Path(sys.argv[1])
for doc in yaml.safe_load_all((root / "shared-proxy.yaml").read_text()):
    if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "shared-nginx-config":
        (root / "nginx.conf").write_text(doc["data"]["nginx.conf"])
PY
podman build --file "$project_root/proxy/Containerfile" --tag "$image" "$project_root"
podman volume create "$volume" >/dev/null
podman run --rm --user root --volume "$volume:/var/lib/todo-tls" \
  --entrypoint chown "$image" nginx:nginx /var/lib/todo-tls
for attempt in 1 2; do
  podman run --rm --add-host todo-app:127.0.0.1 --add-host todo-keycloak:127.0.0.1 \
    --env TODO_TLS_HOSTNAME=todo.test --volume "$volume:/var/lib/todo-tls" \
    --volume "$work_directory/nginx.conf:/etc/todo-nginx/nginx.conf:ro,Z" \
    "$image" nginx -t -c /etc/todo-nginx/nginx.conf
  podman run --rm --volume "$volume:/var/lib/todo-tls:ro" --entrypoint sh "$image" -ec '
    openssl x509 -in /var/lib/todo-tls/server.crt -noout -checkhost todo.test
    openssl verify -CAfile /var/lib/todo-tls/ca.crt /var/lib/todo-tls/server.crt
    test "$(stat -c "%a" /var/lib/todo-tls/ca.key)" = 600
    test "$(stat -c "%a" /var/lib/todo-tls/server.key)" = 600
    sha256sum /var/lib/todo-tls/ca.crt /var/lib/todo-tls/server.crt
  ' > "$work_directory/tls-$attempt"
done
cmp "$work_directory/tls-1" "$work_directory/tls-2"
cat "$work_directory/tls-2"
