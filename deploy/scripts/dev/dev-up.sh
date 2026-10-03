#!/usr/bin/env bash
# Start the development stack: render with the local values and run it with
# podman kube play directly, without systemd. dev-down.sh takes it down.
set -euo pipefail
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
export PYTHONPATH="$project_root/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m app_installer install --mode dev "$@"
