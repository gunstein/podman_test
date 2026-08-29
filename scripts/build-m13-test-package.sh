#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=${1:-"$project_root/dist/todo-m13-test.tar.gz"}
work_directory=$(mktemp -d)
package_directory="$work_directory/todo-m13-test"
trap 'rm -rf "$work_directory"' EXIT

mkdir -p "$package_directory/ansible/roles" \
  "$package_directory/quadlet" \
  "$package_directory/scripts"
mkdir -p "$(dirname "$output")"

cp "$project_root/ansible/"M13*.md \
  "$project_root/ansible/"*-m13.yml \
  "$project_root/ansible/inventory-m13.example.ini" \
  "$project_root/ansible/sync-standby-secrets.yml" \
  "$package_directory/ansible/"
for role in m13_preflight postgres_primary postgres_standby todo_dr; do
  cp -r "$project_root/ansible/roles/$role" \
    "$package_directory/ansible/roles/"
done
cp -r "$project_root/ansible/tasks" "$package_directory/ansible/"
cp "$project_root/quadlet/todo.network" \
  "$project_root/quadlet/todo-postgres-data.volume" \
  "$package_directory/quadlet/"
cp "$project_root/scripts/todo_dr.py" "$package_directory/scripts/"

printf '%s\n' M13.5 > "$package_directory/VERSION"
tar -czf "$output" -C "$work_directory" "$(basename "$package_directory")"
output_directory=$(dirname "$output")
output_name=$(basename "$output")
(
  cd "$output_directory"
  sha256sum "$output_name" > "$output_name.sha256"
)
printf 'Created %s\n' "$output"
printf 'Created %s.sha256\n' "$output"
