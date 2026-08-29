#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$project_root/dist/todo-m14-test.tar.gz"}
work_directory=$(mktemp -d)
package_directory="$work_directory/todo-m14-test"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$package_directory/ansible/roles" "$(dirname "$output")"

cp "$project_root/ansible/M14-FAILOVER.md" \
  "$project_root/ansible/deploy-promoted-m14.yml" \
  "$project_root/ansible/inventory-m14.example.ini" \
  "$package_directory/ansible/"
cp -r "$project_root/ansible/roles/promoted_application" \
  "$package_directory/ansible/roles/"

printf '%s\n' M14 > "$package_directory/VERSION"
tar -czf "$output" -C "$work_directory" "$(basename "$package_directory")"

output_directory=$(dirname "$output")
output_name=$(basename "$output")
(
  cd "$output_directory"
  sha256sum "$output_name" > "$output_name.sha256"
)
printf 'Created %s\n' "$output"
printf 'Created %s.sha256\n' "$output"
