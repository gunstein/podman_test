#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
generated="$project_root/generated/dev"

"$project_root/scripts/render-kube-runtime.sh" \
  "$project_root/helm/todo/values-dev.yaml" "$generated"
podman network exists todo-network || podman network create todo-network
for secret in todo-kube-postgres-secret todo-kube-keycloak-secret \
  todo-kube-backend-secret todo-kube-migrator-secret; do
  podman secret exists "$secret" || {
    printf 'Missing Podman Kube secret: %s\n' "$secret" >&2
    exit 1
  }
done
podman kube play --network todo-network --configmap "$generated/config.yaml" \
  "$generated/postgres.yaml"
podman kube play --network todo-network --configmap "$generated/config.yaml" \
  "$generated/keycloak.yaml"
podman kube play --network todo-network --configmap "$generated/config.yaml" \
  --publish 127.0.0.1:8080:8080 --publish 127.0.0.1:8443:8443 \
  "$generated/app.yaml"
