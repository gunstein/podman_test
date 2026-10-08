#!/usr/bin/env bash
# Validate the actual rendered proxy configuration and persistent TLS bootstrap.
set -euo pipefail
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
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
        (root / "nginx-config").mkdir()
        for name, text in doc["data"].items():
            (root / "nginx-config" / name).write_text(text)
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
    --volume "$work_directory/nginx-config:/etc/todo-nginx:ro,Z" \
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

# Provided mode on the same volume: a request made in the volume, signed by the
# offline CA tool, installed by the installer; nginx then starts with exactly
# that certificate and the demo CA is gone.
printf 'smoke test passphrase\n' > "$work_directory/passphrase"
python3 "$project_root/deploy/scripts/app_ca.py" init --directory "$work_directory/ca" \
  --domain todo.test --domain notes.test --passphrase-file "$work_directory/passphrase"
tls() {
  PYTHONPATH="$project_root/deploy/installer" python3 - "$work_directory" "$volume" "$image" "$@" <<'PY'
import json
import sys
from pathlib import Path

from app_installer import tls

work, volume, image, step = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
where = {'volume': volume, 'image': image, 'kube_runtime_dir': work}
if step == 'request':
    print(json.dumps(tls.request(work / 'host.csr', **where)))
else:
    print(json.dumps(tls.install(work / 'host.crt', work / 'ca/ca.crt', **where)))
PY
}
tls request
python3 "$project_root/deploy/scripts/app_ca.py" sign --directory "$work_directory/ca" \
  --request "$work_directory/host.csr" --output "$work_directory/host.crt" \
  --passphrase-file "$work_directory/passphrase"
test "$(tls install)" = true
test "$(tls install)" = false
podman run --rm \
  --env TODO_TLS_HOSTNAME=todo.test --env "APP_TLS_HOSTNAMES=todo.test notes.test" \
  --volume "$volume:/var/lib/todo-tls" \
  --volume "$work_directory/nginx-config:/etc/todo-nginx:ro,Z" \
  "$image" nginx -t -c /etc/todo-nginx/nginx.conf
podman run --rm --volume "$volume:/var/lib/todo-tls:ro" --entrypoint sh "$image" -ec '
  test "$(cat /var/lib/todo-tls/tls-mode)" = provided
  test ! -e /var/lib/todo-tls/ca.key
  test ! -e /var/lib/todo-tls/request.key
  test "$(stat -c "%a" /var/lib/todo-tls/server.key)" = 600
  cat /var/lib/todo-tls/server.crt
' > "$work_directory/provided.crt"
cmp "$work_directory/provided.crt" "$work_directory/host.crt"
echo 'Provided TLS mode: installed and served as issued'
