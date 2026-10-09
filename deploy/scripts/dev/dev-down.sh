#!/usr/bin/env bash
# Take down what dev-up.sh started; data volumes and secrets stay, the Kube
# secrets' volumes (copies of the passwords that kube play made) go.
set -euo pipefail
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
export PYTHONPATH="$project_root/deploy/installer${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m app_installer down "$@"
