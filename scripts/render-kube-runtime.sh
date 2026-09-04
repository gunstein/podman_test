#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
values_file=${1:-"$project_root/helm/todo/values-prod.yaml"}
output_directory=${2:-"$project_root/kube/runtime"}
helm_command=${HELM:-helm}

mkdir -p "$output_directory"
for manifest in app keycloak postgres config; do
  "$helm_command" template todo "$project_root/helm/todo" \
    --values "$values_file" --show-only "templates/$manifest.yaml" \
    > "$output_directory/$manifest.yaml"
  perl -0pi -e 's/\n+\z/\n/' "$output_directory/$manifest.yaml"
done
