#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$project_root/dist/todo-m16-test.tar.gz"}
work_directory=$(mktemp -d)
package_directory="$work_directory/todo-m16-test"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$package_directory/ansible/roles" \
  "$package_directory/quadlet" \
  "$package_directory/docs" \
  "$(dirname "$output")"

cp "$project_root/ansible.cfg" "$package_directory/"

cp "$project_root/ansible/M16-RESTORE-REDUNDANCY.md" \
  "$project_root/ansible/preflight-m16.yml" \
  "$project_root/ansible/rebuild-standby-m16.yml" \
  "$project_root/ansible/status-m16.yml" \
  "$project_root/ansible/inventory-m16.example.ini" \
  "$project_root/ansible/inventory-cluster.example.ini" \
  "$package_directory/ansible/"
for role in postgres_redundancy_primary postgres_reseed_standby; do
  cp -r "$project_root/ansible/roles/$role" \
    "$package_directory/ansible/roles/"
done
cp "$project_root/quadlet/todo.network" \
  "$project_root/quadlet/todo-postgres-data.volume" \
  "$package_directory/quadlet/"
cp "$project_root/docs/SELINUX.md" \
  "$project_root/docs/SECRETS.md" \
  "$project_root/docs/WHAT-YOU-LEARN.md" \
  "$package_directory/docs/"

printf '%s\n' M16 > "$package_directory/VERSION"
tar -czf "$output" -C "$work_directory" "$(basename "$package_directory")"
output_directory=$(dirname "$output")
output_name=$(basename "$output")
(
  cd "$output_directory"
  sha256sum "$output_name" > "$output_name.sha256"
)
printf 'Created %s\n' "$output"
printf 'Created %s.sha256\n' "$output"
