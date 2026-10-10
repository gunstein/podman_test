#!/usr/bin/env bash
# Render platform.yaml's apps as Kube YAML with Jinja2, on a build host.
# A build-mode install renders the platform it installs itself (install.prepare).
# Usage: render-kube-runtime.sh [ENVIRONMENT] [OUTPUT_DIRECTORY]   (ENVIRONMENT: local or prod)
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
environment=${1:-prod}
output_directory=${2:-"$project_root/generated/kube-runtime"}
export PYTHONPATH="$project_root/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m app_installer.render "$project_root" "$environment" "$output_directory"
