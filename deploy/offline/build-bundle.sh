#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
output=${1:-"$project_root/dist/todo-offline-m12.tar.gz"}
work_directory=$(mktemp -d)
bundle_directory="$work_directory/todo-offline-m12"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$bundle_directory/images" "$bundle_directory/docs" \
  "$bundle_directory/deploy/charts" "$bundle_directory/deploy/runtime" \
  "$bundle_directory/deploy/quadlet" "$bundle_directory/deploy/offline" \
  "$bundle_directory/deploy/ansible/playbooks" "$bundle_directory/deploy/ansible/inventories/local"
mkdir -p "$(dirname "$output")"
"$project_root/deploy/scripts/render-kube-runtime.sh" "$project_root/deploy/environments/prod/values.yaml" "$bundle_directory/generated/kube-runtime"

podman build --pull --file "$project_root/backend/Containerfile" --tag localhost/todo-backend:m12 "$project_root"
podman build --pull --file "$project_root/frontend/Containerfile" --tag localhost/todo-frontend:m12 "$project_root"
podman build --pull --file "$project_root/proxy/Containerfile" --tag localhost/todo-proxy:m12 "$project_root"
podman build --pull --file "$project_root/keycloak/Containerfile" --tag localhost/todo-keycloak:m12 "$project_root"
podman pull docker.io/library/postgres:17.11

podman save --format oci-archive --output "$bundle_directory/images/todo-backend-m12.tar" localhost/todo-backend:m12
podman save --format oci-archive --output "$bundle_directory/images/todo-frontend-m12.tar" localhost/todo-frontend:m12
podman save --format oci-archive --output "$bundle_directory/images/todo-proxy-m12.tar" localhost/todo-proxy:m12
podman save --format oci-archive --output "$bundle_directory/images/todo-keycloak-m12.tar" localhost/todo-keycloak:m12
podman save --format oci-archive --output "$bundle_directory/images/postgres-17.11.tar" docker.io/library/postgres:17.11

cp "$project_root/ansible.cfg" "$bundle_directory/"
cp "$project_root/docs/ARCHITECTURE.md" \
  "$project_root/docs/SECRETS.md" \
  "$project_root/docs/TLS.md" \
  "$project_root/docs/SELINUX.md" \
  "$project_root/docs/WHAT-YOU-LEARN.md" \
  "$project_root/docs/LEARNING-GUIDE.md" \
  "$project_root/docs/ACCEPTANCE.md" \
  "$project_root/docs/ACCEPTANCE-TROUBLESHOOTING.md" \
  "$project_root/docs/PROXMOX-QUARANTINE.md" \
  "$bundle_directory/docs/"

cp "$project_root/deploy/README.md" "$bundle_directory/deploy/"
cp "$project_root/deploy/quadlet/README.md" "$bundle_directory/deploy/quadlet/"
cp "$project_root/deploy/quadlet/todo.network" "$project_root/deploy/quadlet/"*.kube.j2 \
  "$bundle_directory/deploy/quadlet/"
cp -r "$project_root/deploy/charts/shared-proxy" "$project_root/deploy/charts/todo" "$bundle_directory/deploy/charts/"
cp -r "$project_root/deploy/environments" "$bundle_directory/deploy/"
cp "$project_root/deploy/runtime/README.md" "$project_root/deploy/runtime/RESULTS.md" "$bundle_directory/deploy/runtime/"
mkdir -p "$bundle_directory/deploy/ansible/roles"
cp "$project_root/deploy/ansible/playbooks/deploy.yml" \
  "$project_root/deploy/ansible/playbooks/uninstall.yml" \
  "$bundle_directory/deploy/ansible/playbooks/"
cp "$project_root/deploy/ansible/inventories/local/hosts.ini" \
  "$bundle_directory/deploy/ansible/inventories/local/"
cp "$project_root/deploy/ansible/requirements.txt" "$project_root/deploy/ansible/"*.md \
  "$bundle_directory/deploy/ansible/"
cp "$project_root/deploy/offline/install.sh" "$project_root/deploy/offline/preflight.sh" \
  "$bundle_directory/"
cp "$project_root/deploy/offline/README.md" "$project_root/deploy/offline/FAPOLICYD.md" \
  "$bundle_directory/deploy/offline/"

printf '%s\n' '# Todo offline bundle' '' \
  'See [offline installation](deploy/offline/README.md) for verification and installation.' \
  > "$bundle_directory/README.md"

mkdir -p "$bundle_directory/deploy/installer/todo_installer"
cp "$project_root/deploy/installer/README.md" "$project_root/deploy/installer/pyproject.toml" "$bundle_directory/deploy/installer/"
cp "$project_root/deploy/installer/todo_installer/"*.py \
  "$bundle_directory/deploy/installer/todo_installer/"

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
