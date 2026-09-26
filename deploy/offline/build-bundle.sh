#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
output=${1:-"$project_root/dist/todo-offline-m12.tar.gz"}
work_directory=$(mktemp -d)
bundle_directory="$work_directory/todo-offline-m12"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$bundle_directory/images" "$bundle_directory/docs" \
  "$bundle_directory/deploy/runtime" \
  "$bundle_directory/deploy/quadlet" "$bundle_directory/deploy/offline"
mkdir -p "$(dirname "$output")"
"$project_root/deploy/scripts/render-kube-runtime.sh" "$project_root/deploy/environments/prod/values.yaml" "$bundle_directory/generated/kube-runtime"

PYTHONPATH="$project_root/deploy/installer${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m app_installer.images "$project_root" "$bundle_directory/images"

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
cp "$project_root/deploy/quadlet/app-network.network" "$project_root/deploy/quadlet/"*.kube.j2 \
  "$bundle_directory/deploy/quadlet/"
cp -r "$project_root/deploy/environments" "$bundle_directory/deploy/"
cp "$project_root/deploy/runtime/README.md" "$bundle_directory/deploy/runtime/"
cp "$project_root/docs/history/RESULTS.md" "$bundle_directory/deploy/runtime/"
cp "$project_root/deploy/offline/install.sh" "$project_root/deploy/offline/preflight.sh" \
  "$bundle_directory/"
cp "$project_root/deploy/offline/README.md" "$project_root/deploy/offline/FAPOLICYD.md" \
  "$bundle_directory/deploy/offline/"

printf '%s\n' '# Todo offline bundle' '' \
  'See [offline installation](deploy/offline/README.md) for verification and installation.' \
  > "$bundle_directory/README.md"

mkdir -p "$bundle_directory/deploy/installer/app_installer"
cp "$project_root/deploy/installer/README.md" "$project_root/deploy/installer/pyproject.toml" "$bundle_directory/deploy/installer/"
cp "$project_root/deploy/installer/app_installer/"*.py \
  "$bundle_directory/deploy/installer/app_installer/"

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
