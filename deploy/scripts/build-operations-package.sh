#!/usr/bin/env bash
# Build the operations package: app-ops, the DR host tools, the installer and
# the same rendered target files as the offline bundle, plus the docs; no
# image archives. Writes dist/platform-operations.tar.gz and its .sha256.
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
output=${1:-"$project_root/dist/platform-operations.tar.gz"}
work_directory=$(mktemp -d)
package_directory="$work_directory/platform-operations"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$package_directory/deploy/runtime" \
  "$package_directory/deploy/scripts" \
  "$package_directory/deploy/offline" \
  "$package_directory/docs"
mkdir -p "$(dirname "$output")"

cp "$project_root/deploy/README.md" "$package_directory/deploy/"
# The DR tools install the same target files as the offline bundle (bundle.json,
# generated/target); like the bundle, the package has no templates.
PYTHONPATH="$project_root/deploy/installer${PYTHONPATH:+:$PYTHONPATH}" python3 -m app_installer.bundle \
  "$project_root" prod "$package_directory"
cp "$project_root/deploy/runtime/README.md" "$package_directory/deploy/runtime/"
cp "$project_root/docs/history/RESULTS.md" "$package_directory/deploy/runtime/"
# app_ca.py and platform-ca-sign: the CA for provided TLS mode, from its own storage (docs/TLS.md).
cp "$project_root/deploy/scripts/trust-files.sh" \
  "$project_root/deploy/scripts/wait-ready.sh" "$project_root/deploy/scripts/app_ca.py" \
  "$project_root/deploy/scripts/platform-ca-sign" "$package_directory/deploy/scripts/"
cp "$project_root/deploy/offline/FAPOLICYD.md" "$project_root/deploy/offline/README.md" \
  "$package_directory/deploy/offline/"
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
cp -r "$project_root/docs/runbooks" "$package_directory/docs/"

mkdir -p "$package_directory/deploy/installer/app_installer"
cp "$project_root/deploy/installer/README.md" "$project_root/deploy/installer/pyproject.toml" "$package_directory/deploy/installer/"
cp "$project_root/deploy/installer/app_installer/"*.py \
  "$package_directory/deploy/installer/app_installer/"
# DR: app-ops on the controller, app_dr_host and the tools on the hosts, and their docs.
mkdir -p "$package_directory/deploy/dr/app_ops" "$package_directory/deploy/dr/app_dr_host" \
  "$package_directory/deploy/dr/scripts" "$package_directory/deploy/dr/systemd"
cp "$project_root/deploy/dr/"*.md "$package_directory/deploy/dr/"
cp "$project_root/deploy/dr/app_ops/"*.py "$package_directory/deploy/dr/app_ops/"
cp "$project_root/deploy/dr/app_dr_host/"*.py "$package_directory/deploy/dr/app_dr_host/"
cp "$project_root/deploy/dr/scripts/app_dr.py" "$project_root/deploy/dr/scripts/app_backup.py" \
  "$project_root/deploy/dr/scripts/app-quarantine.sh" "$package_directory/deploy/dr/scripts/"
# The scheduled DR check and the nightly backup that app-ops installs as user units.
cp "$project_root/deploy/dr/systemd/"*.service "$project_root/deploy/dr/systemd/"*.timer \
  "$package_directory/deploy/dr/systemd/"

source_revision=unknown
source_state=unknown
if source_revision=$(git -C "$project_root" rev-parse --verify HEAD 2>/dev/null); then
  source_state=clean
  if test -n "$(git -C "$project_root" status --porcelain --untracked-files=normal)"; then
    source_state=dirty
  fi
fi
printf 'package=platform-operations\nsource_revision=%s\nsource_state=%s\n' \
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
