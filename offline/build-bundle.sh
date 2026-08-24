#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$project_root/dist/todo-offline-m12.tar.gz"}
work_directory=$(mktemp -d)
bundle_directory="$work_directory/todo-offline-m12"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$bundle_directory/images" "$bundle_directory/wheels" \
  "$bundle_directory/ansible-runtime"
mkdir -p "$(dirname "$output")"

podman build --pull --file "$project_root/backend/Containerfile" --tag localhost/todo-backend:m12 "$project_root"
podman build --pull --file "$project_root/frontend/Containerfile" --tag localhost/todo-frontend:m12 "$project_root"
podman build --pull --file "$project_root/keycloak/Containerfile" --tag localhost/todo-keycloak:m12 "$project_root"
podman pull docker.io/library/postgres:17.11

podman save --format oci-archive --output "$bundle_directory/images/todo-backend-m12.tar" localhost/todo-backend:m12
podman save --format oci-archive --output "$bundle_directory/images/todo-frontend-m12.tar" localhost/todo-frontend:m12
podman save --format oci-archive --output "$bundle_directory/images/todo-keycloak-m12.tar" localhost/todo-keycloak:m12
podman save --format oci-archive --output "$bundle_directory/images/postgres-17.11.tar" docker.io/library/postgres:17.11

python3 -m pip download --dest "$bundle_directory/wheels" --requirement "$project_root/ansible/requirements.txt"
python3 -m pip install \
  --no-compile \
  --no-index \
  --find-links "$bundle_directory/wheels" \
  --target "$bundle_directory/ansible-runtime" \
  --requirement "$project_root/ansible/requirements.txt"

cp -r "$project_root/quadlet" "$bundle_directory/"
mkdir -p "$bundle_directory/ansible"
cp "$project_root/ansible/"*.yml "$project_root/ansible/inventory.ini" \
  "$project_root/ansible/requirements.txt" "$bundle_directory/ansible/"
cp "$project_root/offline/install.sh" "$project_root/offline/preflight.sh" \
  "$project_root/offline/README.md" "$bundle_directory/"

printf '%s\n' "M12" > "$bundle_directory/VERSION"
python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' \
  > "$bundle_directory/PYTHON_VERSION"
(
  cd "$bundle_directory"
  find . -type f ! -name SHA256SUMS -print0 |
    sort -z |
    xargs -0 sha256sum > SHA256SUMS
)

tar -czf "$output" -C "$work_directory" "$(basename "$bundle_directory")"
printf 'Created %s\n' "$output"
