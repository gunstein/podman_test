#!/usr/bin/env bash

set -euo pipefail

project_name=todo-compose-probe
container_name=todo-compose-probe
secret_name=todo-compose-probe-secret
volume_name=todo-compose-probe-data
network_name=${project_name}_default
provider=${TODO_COMPOSE_PROVIDER:-/usr/bin/podman-compose}
repository_directory=${TODO_COMPOSE_REPOSITORY_DIRECTORY:-}

if test -z "$repository_directory"; then
  repository_directory=$(
    cd "$(dirname "$0")/.." &&
      pwd
  )
fi
compose_file=$repository_directory/compose/prototype/compose.yaml
health=missing

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  exit_status=$?
  trap - EXIT INT TERM

  PODMAN_COMPOSE_PROVIDER="$provider" \
    podman compose \
      --project-name "$project_name" \
      --file "$compose_file" \
      down \
    >/dev/null 2>&1 || true

  podman rm --force "$container_name" >/dev/null 2>&1 || true
  podman secret rm "$secret_name" >/dev/null 2>&1 || true
  podman volume rm "$volume_name" >/dev/null 2>&1 || true
  podman network rm "$network_name" >/dev/null 2>&1 || true

  exit "$exit_status"
}

command -v podman >/dev/null ||
  fail "podman is required"

test -x "$provider" ||
  fail "Compose provider is not executable: $provider"

test -f "$compose_file" ||
  fail "Compose prototype is missing: $compose_file"

podman container exists "$container_name" &&
  fail "Refusing to reuse existing container: $container_name"

podman secret exists "$secret_name" &&
  fail "Refusing to reuse existing secret: $secret_name"

podman volume exists "$volume_name" &&
  fail "Refusing to reuse existing volume: $volume_name"

podman network exists "$network_name" &&
  fail "Refusing to reuse existing network: $network_name"

trap cleanup EXIT INT TERM

printf '%s\n' '--- Explicit Compose provider ---'
PODMAN_COMPOSE_PROVIDER="$provider" podman compose version

provider_output=$("$provider" version 2>&1)

if [[ $provider_output =~ podman-compose[[:space:]]version:[[:space:]]([0-9.]+) ]]; then
  provider_version=${BASH_REMATCH[1]}
else
  fail "Could not read podman-compose version"
fi

printf 'provider_path=%s\n' "$provider"
printf 'provider_version=%s\n' "$provider_version"

openssl rand -hex 32 |
  podman secret create "$secret_name" - \
  >/dev/null

podman volume create "$volume_name" >/dev/null

printf '%s\n' '--- Render Compose model ---'
PODMAN_COMPOSE_PROVIDER="$provider" \
  podman compose \
    --project-name "$project_name" \
    --file "$compose_file" \
    config \
  >/dev/null

printf '%s\n' '--- Start rootless probe ---'
PODMAN_COMPOSE_PROVIDER="$provider" \
  podman compose \
    --project-name "$project_name" \
    --file "$compose_file" \
    up --detach

for _ in $(seq 1 30); do
  health=$(
    podman inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' \
      "$container_name"
  )

  case "$health" in
    healthy)
      break
      ;;
    unhealthy)
      podman logs "$container_name" >&2 || true
      fail "Compose probe became unhealthy"
      ;;
  esac

  sleep 1
done

test "$health" = healthy ||
  fail "Compose probe did not become healthy"

podman exec "$container_name" \
  /bin/sh -ec '
    test -s /run/secrets/todo-compose-probe-secret
    test "$(stat -c %u /run/secrets/todo-compose-probe-secret)" = 999
    test "$(stat -c %g /run/secrets/todo-compose-probe-secret)" = 999
    test "$(stat -c %a /run/secrets/todo-compose-probe-secret)" = 440
    test "$(cat /probe/result)" = "compose probe"
  '

test "$(
  podman inspect \
    --format '{{index .Config.Labels "io.podman.compose.project"}}' \
    "$container_name"
)" = "$project_name" ||
  fail "Container is not owned by the expected Compose project"

printf '%s\n' 'Compose provider probe passed.'
