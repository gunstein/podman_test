#!/usr/bin/env bash
# Validate the actual rendered proxy configuration and nginx's TLS files, first in
# the TLS volume (app_installer/tls.py), then as Podman secrets (tls_secrets.py).
set -euo pipefail
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
work_directory=$(mktemp -d)
image="localhost/platform-proxy-smoke:$$"
volume="platform-proxy-smoke-$$"
# The Podman secrets of this run only, never the host's own nginx secrets.
secret_prefix="platform-proxy-smoke-$$-"
cleanup() {
  podman secret ls --format '{{.Name}}' | grep "^$secret_prefix" | xargs -r podman secret rm >/dev/null 2>&1 || true
  podman volume rm --force "$volume" >/dev/null 2>&1 || true
  podman image rm "$image" >/dev/null 2>&1 || true
  rm -rf "$work_directory"
}
trap cleanup EXIT
"$project_root/deploy/scripts/render-kube-runtime.sh" prod "$work_directory"
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
podman run --rm --user root --volume "$volume:/var/lib/platform-tls" \
  --entrypoint chown "$image" nginx:nginx /var/lib/platform-tls
for attempt in 1 2 3; do
  hostnames=todo.test
  if [[ "$attempt" != 1 ]]; then hostnames="todo.test notes.test"; fi
  podman run --rm \
    --env PLATFORM_TLS_HOSTNAME=auth.test --env "APP_TLS_HOSTNAMES=$hostnames" \
    --volume "$volume:/var/lib/platform-tls" \
    --volume "$work_directory/nginx-config:/etc/platform-nginx:ro,Z" \
    "$image" nginx -t -c /etc/platform-nginx/nginx.conf
  podman run --rm --env "APP_TLS_HOSTNAMES=$hostnames" \
    --volume "$volume:/var/lib/platform-tls:ro" --entrypoint sh "$image" -ec '
    for name in $APP_TLS_HOSTNAMES; do
      openssl verify -CAfile /var/lib/platform-tls/ca.crt -verify_hostname "$name" \
        /var/lib/platform-tls/server.crt >/dev/null
    done
    test "$(stat -c "%a" /var/lib/platform-tls/ca.key)" = 600
    test "$(stat -c "%a" /var/lib/platform-tls/server.key)" = 600
    sha256sum /var/lib/platform-tls/ca.crt /var/lib/platform-tls/server.crt
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

# nginx itself, as in the pod: PLATFORM_TLS_ROLE=serve with the volume read-only.
serve_read_only() {
  podman run --rm --env PLATFORM_TLS_ROLE=serve \
    --env PLATFORM_TLS_HOSTNAME=auth.test --env "APP_TLS_HOSTNAMES=todo.test notes.test" \
    --volume "$volume:/var/lib/platform-tls:ro" \
    --volume "$work_directory/nginx-config:/etc/platform-nginx:ro,Z" \
    "$image" nginx -t -c /etc/platform-nginx/nginx.conf
}
serve_read_only

# Provided mode on the same volume, with the CA on this same host: a request
# made in the volume, signed by app_ca.py from its own directory, installed by
# the installer; nginx then starts with exactly that certificate, read-only,
# and the demo CA is gone.
printf 'smoke test passphrase\n' > "$work_directory/passphrase"
python3 "$project_root/deploy/scripts/app_ca.py" init --directory "$work_directory/ca" \
  --domain auth.test --domain todo.test --domain notes.test --passphrase-file "$work_directory/passphrase"
tls() {
  PYTHONPATH="$project_root/deploy/installer" python3 - "$work_directory" "$volume" "$image" "$@" <<'PY'
import json
import sys
from pathlib import Path

from app_installer import tls

work, volume, image, step = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
where = {'volume': volume, 'image': image, 'hostnames': ['auth.test', 'todo.test', 'notes.test']}
if step == 'request':
    print(json.dumps(tls.request(work / 'host.csr', **where)))
elif step == 'status':
    # The image's own entrypoint check (PLATFORM_TLS_ROLE=check): mode and problem, not the days.
    current, days, problem = tls.status(where.pop('hostnames'), **where)
    print(current, days is not None, problem or 'fits')
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
test "$(tls status)" = 'provided True fits'
podman run --rm \
  --env PLATFORM_TLS_HOSTNAME=auth.test --env "APP_TLS_HOSTNAMES=todo.test notes.test" \
  --volume "$volume:/var/lib/platform-tls" \
  --volume "$work_directory/nginx-config:/etc/platform-nginx:ro,Z" \
  "$image" nginx -t -c /etc/platform-nginx/nginx.conf
podman run --rm --volume "$volume:/var/lib/platform-tls:ro" --entrypoint sh "$image" -ec '
  test "$(cat /var/lib/platform-tls/tls-mode)" = provided
  test ! -e /var/lib/platform-tls/ca.key
  test ! -e /var/lib/platform-tls/request.key
  test "$(stat -c "%a" /var/lib/platform-tls/server.key)" = 600
  cat /var/lib/platform-tls/server.crt
' > "$work_directory/provided.crt"
cmp "$work_directory/provided.crt" "$work_directory/host.crt"
serve_read_only
echo 'Provided TLS mode: installed and served as issued'

# Podman secrets, the default: the same two modes with every file a secret,
# made by the installer in throwaway containers of this image. nginx gets the
# four files it serves (tls-mode, ca.crt, server.crt, server.key) read-only,
# here as --secret mounts where the pod mounts its Kube secret.
secrets_step() {
  PYTHONPATH="$project_root/deploy/installer" python3 - "$work_directory" "$image" "$secret_prefix" "$@" <<'PY'
import json
import sys
from pathlib import Path

from app_installer import apps, tls_secrets

work, image, prefix, step = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
apps.PROXY_IMAGE = image
tls_secrets.FILES = {name: prefix + secret for name, secret in tls_secrets.FILES.items()}
tls_secrets.KUBE_SECRET = prefix + 'kube-tls'
names = ['auth.test', 'todo.test', 'notes.test']  # Keycloak's first, as recorded_hostnames gives them
if step == 'provision':
    print(json.dumps(tls_secrets.provision(names)))
elif step == 'mounts':
    # What the pod's Kube secret volume would hold, as --secret mounts.
    print(' '.join(f'--secret={tls_secrets.FILES[name]},type=mount,target=/var/lib/platform-tls/{name},'
                   'uid=101,gid=101,mode=0444' for name in tls_secrets.SERVED))
elif step == 'request':
    print(json.dumps(tls_secrets.request(work / 'secret-host.csr', hostnames=names)))
elif step == 'install':
    print(json.dumps(tls_secrets.install(work / 'secret-host.crt', work / 'ca/ca.crt', hostnames=names)))
elif step == 'status':
    current, days, problem = tls_secrets.status(names)
    print(current, days is not None, problem or 'fits')
else:
    print(' '.join(sorted(name for name in tls_secrets.FILES if tls_secrets.has(name))))
PY
}
serve_secrets() {
  # shellcheck disable=SC2046 # one --secret option per word
  podman run --rm --env PLATFORM_TLS_ROLE=serve \
    --env PLATFORM_TLS_HOSTNAME=auth.test --env "APP_TLS_HOSTNAMES=todo.test notes.test" \
    $(secrets_step mounts) \
    --volume "$work_directory/nginx-config:/etc/platform-nginx:ro,Z" \
    "$image" nginx -t -c /etc/platform-nginx/nginx.conf
}
test "$(secrets_step provision)" = true
test "$(secrets_step provision)" = false
test "$(secrets_step status)" = 'local True fits'
test "$(secrets_step present)" = 'ca.crt ca.key server.crt server.key tls-mode'
serve_secrets
secrets_step request
python3 "$project_root/deploy/scripts/app_ca.py" sign --directory "$work_directory/ca" \
  --request "$work_directory/secret-host.csr" --output "$work_directory/secret-host.crt" \
  --passphrase-file "$work_directory/passphrase"
test "$(secrets_step install)" = true
test "$(secrets_step install)" = false
test "$(secrets_step status)" = 'provided True fits'
# The demo CA's key and the waiting key are gone; nginx serves the issued certificate.
test "$(secrets_step present)" = 'ca.crt server.crt server.key tls-mode'
test "$(podman secret inspect --showsecret --format '{{.SecretData}}' "${secret_prefix}platform-proxy-tls-cert" \
  | openssl x509 -noout -fingerprint -sha256)" = \
  "$(openssl x509 -in "$work_directory/secret-host.crt" -noout -fingerprint -sha256)"
serve_secrets
echo 'Podman secrets: local and provided TLS mode served as made and issued'
