#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$project_root/dist/todo-offline-m12.tar.gz"}
work_directory=$(mktemp -d)
bundle_directory="$work_directory/todo-offline-m12"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$bundle_directory/images" "$bundle_directory/docs" \
  "$bundle_directory/kube" "$bundle_directory/helm"
mkdir -p "$(dirname "$output")"
"$project_root/scripts/render-kube-runtime.sh"

podman build --pull --file "$project_root/backend/Containerfile" --tag localhost/todo-backend:m12 "$project_root"
podman build --pull --file "$project_root/frontend/Containerfile" --tag localhost/todo-frontend:m12 "$project_root"
podman build --pull --file "$project_root/keycloak/Containerfile" --tag localhost/todo-keycloak:m12 "$project_root"
podman pull docker.io/library/postgres:17.11

podman save --format oci-archive --output "$bundle_directory/images/todo-backend-m12.tar" localhost/todo-backend:m12
podman save --format oci-archive --output "$bundle_directory/images/todo-frontend-m12.tar" localhost/todo-frontend:m12
podman save --format oci-archive --output "$bundle_directory/images/todo-keycloak-m12.tar" localhost/todo-keycloak:m12
podman save --format oci-archive --output "$bundle_directory/images/postgres-17.11.tar" docker.io/library/postgres:17.11

cp "$project_root/ansible.cfg" "$bundle_directory/"
cp "$project_root/docs/SECRETS.md" \
  "$project_root/docs/TLS.md" \
  "$project_root/docs/SELINUX.md" \
  "$project_root/docs/WHAT-YOU-LEARN.md" \
  "$project_root/docs/LEARNING-GUIDE.md" \
  "$project_root/docs/LAB-ACCEPTANCE.md" \
  "$bundle_directory/docs/"

mkdir -p "$bundle_directory/quadlet"
cp "$project_root/quadlet/todo.network" \
  "$project_root/quadlet/todo-postgres-data.volume" \
  "$project_root/quadlet/todo-postgres-backup.volume" \
  "$project_root/quadlet/todo-nginx-data.volume" \
  "$bundle_directory/quadlet/"
cp -r "$project_root/helm/todo" "$bundle_directory/helm/"
cp -r "$project_root/kube/runtime" "$bundle_directory/kube/"
mkdir -p "$bundle_directory/ansible"
mkdir -p "$bundle_directory/ansible/roles"
cp "$project_root/ansible/deploy.yml" \
  "$project_root/ansible/uninstall.yml" \
  "$project_root/ansible/inventory.ini" \
  "$project_root/ansible/requirements.txt" \
  "$bundle_directory/ansible/"
cp -r "$project_root/ansible/roles/todo_kube_runtime" \
  "$bundle_directory/ansible/roles/"
cp "$project_root/offline/install.sh" "$project_root/offline/preflight.sh" \
  "$project_root/offline/README.md" "$project_root/offline/FAPOLICYD.md" \
  "$bundle_directory/"

source_revision=unknown
source_state=unknown
if source_revision=$(git -C "$project_root" rev-parse --verify HEAD 2>/dev/null); then
  source_state=clean
  if test -n "$(git -C "$project_root" status --porcelain --untracked-files=normal)"; then
    source_state=dirty
  fi
fi
printf 'package=todo-offline-m12\nsource_revision=%s\nsource_state=%s\n' \
  "$source_revision" "$source_state" > "$bundle_directory/VERSION"
(
  cd "$bundle_directory"
  find . -type f ! -name SHA256SUMS -print0 |
    sort -z |
    xargs -0 sha256sum > "$work_directory/SHA256SUMS"
  mv "$work_directory/SHA256SUMS" SHA256SUMS
)

tar -czf "$output" -C "$work_directory" "$(basename "$bundle_directory")"
output_directory=$(dirname "$output")
output_name=$(basename "$output")
(
  cd "$output_directory"
  sha256sum "$output_name" > "$output_name.sha256"
)
printf 'Created %s\n' "$output"
printf 'Created %s.sha256\n' "$output"
