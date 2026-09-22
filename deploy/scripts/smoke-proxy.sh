#!/usr/bin/env bash
# Validate the actual rendered proxy configuration and persistent TLS bootstrap.
set -euo pipefail
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work_directory=$(mktemp -d)
image="localhost/todo-proxy-smoke:$$"
volume="todo-proxy-smoke-$$"
cleanup() {
  podman volume rm --force "$volume" >/dev/null 2>&1 || true
  podman image rm "$image" >/dev/null 2>&1 || true
  rm -rf "$work_directory"
}
trap cleanup EXIT
"$project_root/deploy/scripts/render-kube-runtime.sh" "$project_root/deploy/environments/prod/values.yaml" "$work_directory"
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
for attempt in 1 2 3; do
  hostnames=todo.test
  if [[ "$attempt" != 1 ]]; then hostnames="todo.test notes.test"; fi
  podman run --rm \
    --env TODO_TLS_HOSTNAME=todo.test --env "APP_TLS_HOSTNAMES=$hostnames" \
    --volume "$volume:/var/lib/todo-tls" \
    --volume "$work_directory/nginx.conf:/etc/todo-nginx/nginx.conf:ro,Z" \
    "$image" nginx -t -c /etc/todo-nginx/nginx.conf
  podman run --rm --env "APP_TLS_HOSTNAMES=$hostnames" \
    --volume "$volume:/var/lib/todo-tls:ro" --entrypoint sh "$image" -ec '
    for name in $APP_TLS_HOSTNAMES; do
      openssl verify -CAfile /var/lib/todo-tls/ca.crt -verify_hostname "$name" \
        /var/lib/todo-tls/server.crt >/dev/null
    done
    test "$(stat -c "%a" /var/lib/todo-tls/ca.key)" = 600
    test "$(stat -c "%a" /var/lib/todo-tls/server.key)" = 600
    sha256sum /var/lib/todo-tls/ca.crt /var/lib/todo-tls/server.crt
  ' > "$work_directory/tls-$attempt"
done
# Expanding the SAN renews only the leaf; the next start changes neither certificate.
head -n 1 "$work_directory/tls-1" > "$work_directory/ca-before"
head -n 1 "$work_directory/tls-2" > "$work_directory/ca-after"
cmp "$work_directory/ca-before" "$work_directory/ca-after"
if cmp -s "$work_directory/tls-1" "$work_directory/tls-2"; then
  echo 'Expected leaf renewal when adding notes.test' >&2
  exit 1
fi
cmp "$work_directory/tls-2" "$work_directory/tls-3"
cat "$work_directory/tls-3"
