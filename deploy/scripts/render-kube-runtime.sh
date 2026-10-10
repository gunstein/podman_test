#!/usr/bin/env bash
# Render the registry's apps (app_installer.apps.registry) as Kube YAML with Jinja2, on a build host.
# A build-mode install renders the platform it installs itself (install.prepare).
# Usage: render-kube-runtime.sh [VALUES_FILE] [OUTPUT_DIRECTORY]
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
values_file=${1:-"$project_root/deploy/environments/prod/values.yaml"}
output_directory=${2:-"$project_root/generated/kube-runtime"}
export PYTHONPATH="$project_root/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m app_installer.render "$project_root" "$values_file" "$output_directory"
