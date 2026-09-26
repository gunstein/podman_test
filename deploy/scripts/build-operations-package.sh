#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
output=${1:-"$project_root/dist/todo-operations.tar.gz"}
work_directory=$(mktemp -d)
package_directory="$work_directory/todo-operations"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$package_directory/deploy/quadlet" \
  "$package_directory/deploy/runtime" \
  "$package_directory/deploy/scripts" \
  "$package_directory/deploy/offline" \
  "$package_directory/docs"
mkdir -p "$(dirname "$output")"

cp "$project_root/deploy/README.md" "$package_directory/deploy/"
cp "$project_root/deploy/quadlet/app-network.network" "$project_root/deploy/quadlet/"*.kube.j2 \
  "$package_directory/deploy/quadlet/"
"$project_root/deploy/scripts/render-kube-runtime.sh" "$project_root/deploy/environments/prod/values.yaml" "$package_directory/generated/kube-runtime"
cp "$project_root/deploy/runtime/README.md" "$package_directory/deploy/runtime/"
cp "$project_root/docs/history/RESULTS.md" "$package_directory/deploy/runtime/"
cp "$project_root/deploy/scripts/app_dr.py" \
  "$project_root/deploy/scripts/app-quarantine.sh" \
  "$project_root/deploy/scripts/trust-files.sh" \
  "$project_root/deploy/scripts/app_backup.py" "$package_directory/deploy/scripts/"
cp "$project_root/deploy/offline/FAPOLICYD.md" "$project_root/deploy/offline/README.md" \
  "$package_directory/deploy/offline/"
cp "$project_root/deploy/quadlet/README.md" "$package_directory/deploy/quadlet/"
cp "$project_root/docs/ARCHITECTURE.md" \
  "$project_root/docs/SECRETS.md" \
  "$project_root/docs/TLS.md" \
  "$project_root/docs/SELINUX.md" \
  "$project_root/docs/WHAT-YOU-LEARN.md" \
  "$project_root/docs/LEARNING-GUIDE.md" \
  "$project_root/docs/ACCEPTANCE.md" \
  "$project_root/docs/ACCEPTANCE-TROUBLESHOOTING.md" \
  "$project_root/docs/PROXMOX-QUARANTINE.md" \
  "$package_directory/docs/"

mkdir -p "$package_directory/deploy/installer/app_installer"
cp "$project_root/deploy/installer/README.md" "$project_root/deploy/installer/pyproject.toml" "$package_directory/deploy/installer/"
cp "$project_root/deploy/installer/app_installer/"*.py \
  "$package_directory/deploy/installer/app_installer/"
mkdir -p "$package_directory/deploy/ops/app_ops"
cp "$project_root/deploy/ops/"*.md "$package_directory/deploy/ops/"
cp "$project_root/deploy/ops/app_ops/"*.py "$package_directory/deploy/ops/app_ops/"

source_revision=unknown
source_state=unknown
if source_revision=$(git -C "$project_root" rev-parse --verify HEAD 2>/dev/null); then
  source_state=clean
  if test -n "$(git -C "$project_root" status --porcelain --untracked-files=normal)"; then
    source_state=dirty
  fi
fi
printf 'package=todo-operations\nsource_revision=%s\nsource_state=%s\n' \
  "$source_revision" "$source_state" > "$package_directory/VERSION"
(
  cd "$package_directory"
  find . -type f ! -name SHA256SUMS -print0 |
    sort -z |
    xargs -0 sha256sum > "$work_directory/SHA256SUMS"
  mv "$work_directory/SHA256SUMS" SHA256SUMS
)
tar -czf "$output" -C "$work_directory" "$(basename "$package_directory")"
output_directory=$(dirname "$output")
output_name=$(basename "$output")
(
  cd "$output_directory"
  sha256sum "$output_name" > "$output_name.sha256"
)
printf 'Created %s\n' "$output"
printf 'Created %s.sha256\n' "$output"
