#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$project_root/dist/todo-m15-test.tar.gz"}
work_directory=$(mktemp -d)
package_directory="$work_directory/todo-m15-test"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$package_directory/ansible/roles" \
  "$package_directory/scripts" \
  "$package_directory/offline" \
  "$package_directory/docs" \
  "$(dirname "$output")"

cp "$project_root/ansible.cfg" "$package_directory/"

cp "$project_root/ansible/M15-BACKUP-PITR.md" \
  "$project_root/ansible/configure-backup-m15.yml" \
  "$project_root/ansible/inventory-m14.example.ini" \
  "$package_directory/ansible/"
cp -r "$project_root/ansible/roles/postgres_backup" \
  "$package_directory/ansible/roles/"
cp "$project_root/scripts/todo_backup.py" "$package_directory/scripts/"
cp "$project_root/offline/FAPOLICYD.md" "$package_directory/offline/"
cp "$project_root/docs/SECRETS.md" \
  "$project_root/docs/TLS.md" \
  "$project_root/docs/SELINUX.md" \
  "$project_root/docs/WHAT-YOU-LEARN.md" \
  "$package_directory/docs/"

printf '%s\n' M15 > "$package_directory/VERSION"
tar -czf "$output" -C "$work_directory" "$(basename "$package_directory")"

output_directory=$(dirname "$output")
output_name=$(basename "$output")
(
  cd "$output_directory"
  sha256sum "$output_name" > "$output_name.sha256"
)
printf 'Created %s\n' "$output"
printf 'Created %s.sha256\n' "$output"
