#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
generated="$project_root/generated/dev"

for manifest in app keycloak postgres; do
  test ! -f "$generated/$manifest.yaml" || \
    podman kube play --down "$generated/$manifest.yaml"
done
