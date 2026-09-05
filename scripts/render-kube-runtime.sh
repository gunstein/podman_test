#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
values_file=${1:-"$project_root/helm/todo/values-prod.yaml"}
output_directory=${2:-"$project_root/kube/runtime"}
helm_command=${HELM:-helm}

command -v "$helm_command" >/dev/null || {
  echo "Helm is required to render the Kube runtime: $helm_command" >&2
  exit 1
}
work_directory=$(mktemp -d)
trap 'rm -rf "$work_directory"' EXIT
for manifest in app keycloak postgres config; do
  "$helm_command" template todo "$project_root/helm/todo" \
    --values "$values_file" --show-only "templates/$manifest.yaml" \
    > "$work_directory/$manifest.yaml"
  perl -0pi -e 's/\n+\z/\n/' "$work_directory/$manifest.yaml"
done

mkdir -p "$output_directory"
for manifest in app keycloak postgres config; do
  cp "$work_directory/$manifest.yaml" "$output_directory/$manifest.yaml"
done
