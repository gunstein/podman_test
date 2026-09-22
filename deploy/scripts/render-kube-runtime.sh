#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
values_file=${1:-"$project_root/deploy/environments/prod/values.yaml"}
output_directory=${2:-"$project_root/generated/kube-runtime"}
export PYTHONPATH="$project_root/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m todo_installer.render "$project_root" "$values_file" "$output_directory" "${3:-}"
