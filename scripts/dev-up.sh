#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
generated="$project_root/generated/dev"

"$project_root/scripts/render-kube-runtime.sh" \
  "$project_root/helm/todo/values-dev.yaml" "$generated"
podman network exists todo-network || podman network create todo-network
for secret in todo-db-password todo-migrator-password todo-app-password \
  todo-keycloak-db-password todo-kube-postgres-secret \
  todo-kube-keycloak-secret todo-kube-backend-secret \
  todo-kube-migrator-secret; do
  podman secret exists "$secret" || {
    printf 'Missing Podman secret: %s\n' "$secret" >&2
    exit 1
  }
done
setup_roles() {
  podman run --rm --network todo-network \
    --secret todo-db-password --secret todo-migrator-password \
    --secret todo-app-password --secret todo-keycloak-db-password \
    --env DATABASE_HOST=todo-postgres --env DATABASE_NAME=todo \
    --env DATABASE_BOOTSTRAP_USER=todo \
    --security-opt no-new-privileges --cap-drop ALL \
    localhost/todo-backend:m12 python -m backend.setup_roles
}

podman kube play --no-pod-prefix --network todo-network \
  --configmap "$generated/config.yaml" \
  "$generated/postgres.yaml"
podman wait --condition healthy todo-postgres >/dev/null
setup_roles
podman kube play --no-pod-prefix --network todo-network --configmap "$generated/config.yaml" \
  "$generated/keycloak.yaml"
podman kube play --no-pod-prefix --network todo-network --configmap "$generated/config.yaml" \
  --publish 127.0.0.1:8080:8080 --publish 127.0.0.1:8443:8443 \
  "$generated/app.yaml"
setup_roles
