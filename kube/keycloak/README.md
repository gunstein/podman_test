# Keycloak Kube candidate

This candidate joins the accepted Keycloak deployment temporarily as a second
node. Keycloak production mode uses distributed Infinispan caching and the
`jdbc-ping` discovery stack, so nodes sharing this database and network form
one cluster. The candidate makes these defaults explicit and has the stable
node name `todo-kube-keycloak`.

This is not a second independent Keycloak cluster. Do not run the candidate
against a database owned by an unrelated Keycloak installation.

The existing raw Podman secrets remain the runtime sources. Their values are
wrapped in one Kube-compatible Podman secret in shell memory; no plaintext file
is created. Keycloak still receives the two values as environment variables,
matching the accepted runtime contract.

The image intentionally uses UID 1000 with primary GID 0. This preserves the
official image's file-access model; group 0 is namespaced by rootless Podman and
does not grant host root privileges.

## Install

Run on the current primary while the accepted stack is healthy:

```bash
podman image exists localhost/todo-keycloak:m12
podman network exists todo-network
podman secret exists todo-keycloak-db-password
podman secret exists todo-keycloak-admin-password
test "$(systemctl --user is-active todo-postgres.service)" = active
test "$(systemctl --user is-active todo-keycloak.service)" = active

test ! -e "$HOME/.config/containers/systemd/todo-kube-keycloak" || {
  echo "STOP: candidate unit directory already exists" >&2
  exit 1
}

if podman pod exists todo-kube-keycloak ||
   podman secret exists todo-kube-keycloak-secret
then
  echo "STOP: candidate Podman state already exists" >&2
  exit 1
fi

todo_kube_keycloak_db_password=$(
  podman secret inspect \
    --showsecret \
    --format '{{.SecretData}}' \
    todo-keycloak-db-password
)

todo_kube_keycloak_admin_password=$(
  podman secret inspect \
    --showsecret \
    --format '{{.SecretData}}' \
    todo-keycloak-admin-password
)

todo_kube_keycloak_db_encoded=$(
  printf '%s' "$todo_kube_keycloak_db_password" |
    base64 -w0
)

todo_kube_keycloak_admin_encoded=$(
  printf '%s' "$todo_kube_keycloak_admin_password" |
    base64 -w0
)

unset \
  todo_kube_keycloak_db_password \
  todo_kube_keycloak_admin_password

printf '%s%s%s%s%s%s\n' \
  '{"apiVersion":"v1","kind":"Secret","metadata":' \
  '{"name":"todo-kube-keycloak-secret"},"data":{"database-password":"' \
  "$todo_kube_keycloak_db_encoded" \
  '","bootstrap-admin-password":"' \
  "$todo_kube_keycloak_admin_encoded" \
  '"}}' |
  podman secret create todo-kube-keycloak-secret -

unset \
  todo_kube_keycloak_db_encoded \
  todo_kube_keycloak_admin_encoded

install -d -m 0700 \
  "$HOME/.config/containers/systemd/todo-kube-keycloak"

install -m 0644 \
  keycloak.yaml \
  config-lab.yaml \
  todo-kube-keycloak.kube \
  "$HOME/.config/containers/systemd/todo-kube-keycloak/"

systemctl --user daemon-reload
systemctl --user start todo-kube-keycloak.service
```

## Verify

Wait for liveness and then perform an explicit readiness request from the Todo
network. The two checks have different meanings.

```bash
for attempt in {1..120}
do
  if podman container exists todo-kube-keycloak-keycloak
  then
    health=$(
      podman inspect \
        --format '{{.State.Health.Status}}' \
        todo-kube-keycloak-keycloak
    )
  else
    health=absent
  fi

  printf 'Health: %s\n' "$health"
  test "$health" = healthy && break
  sleep 1
done

test "$health" = healthy &&
  echo "Keycloak liveness: OK"

unset health

podman run --rm \
  --network todo-network \
  --entrypoint python \
  localhost/todo-backend:m12 \
  -c '
from urllib.request import urlopen

response = urlopen(
    "http://todo-kube-keycloak:9000/auth/health/ready",
    timeout=5,
)
print(response.read().decode())
assert response.status == 200
'

podman run --rm \
  --network todo-network \
  --entrypoint python \
  localhost/todo-backend:m12 \
  -c '
import json
from urllib.request import urlopen

data = json.load(
    urlopen(
        "http://todo-kube-keycloak:8080/auth/realms/todo/.well-known/openid-configuration",
        timeout=5,
    )
)
print(data["issuer"])
assert data["issuer"] == "https://todo.test:8443/auth/realms/todo"
'

podman exec todo-kube-keycloak-keycloak \
  /usr/bin/bash -c \
  'test -n "$KCRAW_DB_PASSWORD" &&
   test -n "$KCRAW_BOOTSTRAP_ADMIN_PASSWORD" &&
   echo "Secret environment: OK"'

podman exec todo-kube-keycloak-keycloak id

podman logs todo-kube-keycloak-keycloak 2>&1 |
  grep -E 'jdbc-ping|JDBC_PING|ISPN000094|todo-kube-keycloak' |
  tail -20
```

The accepted node and application must remain available:

```bash
systemctl --user is-active \
  todo-keycloak.service \
  todo-frontend.service

curl --fail http://127.0.0.1:8080/ready
echo
```

## Cleanup

Stop Keycloak through systemd so it can leave the cluster gracefully:

```bash
systemctl --user stop todo-kube-keycloak.service

sudo journalctl \
  CONTAINER_NAME=todo-kube-keycloak-keycloak \
  --since '-5 minutes' \
  --no-pager |
  grep -F 'Keycloak stopped'

rm -f \
  "$HOME/.config/containers/systemd/todo-kube-keycloak/keycloak.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-keycloak/config-lab.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-keycloak/todo-kube-keycloak.kube"

rmdir "$HOME/.config/containers/systemd/todo-kube-keycloak"
systemctl --user daemon-reload

podman secret rm todo-kube-keycloak-secret

systemctl --user is-active \
  todo-postgres.service \
  todo-backend.service \
  todo-keycloak.service \
  todo-frontend.service

systemctl --user --failed --no-pager
curl --fail http://127.0.0.1:8080/ready
echo
```
