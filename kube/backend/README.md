# Backend Kube candidate

This is the first real Todo workload expressed as Podman Kube YAML. It runs in
parallel with the accepted backend and does not replace it.

The candidate deliberately:

- uses the existing `localhost/todo-backend:m12` image;
- joins the existing `todo-network` but has the distinct DNS name
  `todo-kube-backend`;
- publishes no host port, avoiding a collision with the accepted backend;
- reads non-secret settings from an external ConfigMap;
- reads its database password from an external Kube-compatible Podman secret;
- runs as UID and GID 1000 with all capabilities dropped;
- propagates container failure to systemd and lets systemd restart the
  workload.

The existing raw `todo-app-password` Podman secret is the surviving runtime
source. For this isolated test, its value is wrapped in a Kubernetes Secret
document in shell memory and stored as `todo-kube-backend-secret`. No plaintext
file is created.

## Install the candidate

Run on the current primary while the accepted Todo stack is healthy:

```bash
test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
podman image exists localhost/todo-backend:m12
podman network exists todo-network
podman secret exists todo-app-password
test "$(systemctl --user is-active todo-postgres.service)" = active

test ! -e "$HOME/.config/containers/systemd/todo-kube-backend" || {
  echo "STOP: candidate unit directory already exists" >&2
  exit 1
}

if podman secret exists todo-kube-backend-secret
then
  echo "STOP: candidate secret already exists" >&2
  exit 1
fi

todo_kube_backend_password=$(
  podman secret inspect \
    --showsecret \
    --format '{{.SecretData}}' \
    todo-app-password
)

todo_kube_backend_encoded=$(
  printf '%s' "$todo_kube_backend_password" |
    base64 -w0
)

unset todo_kube_backend_password

printf '%s\n' \
  '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"todo-kube-backend-secret"},"data":{"database-password":"'"$todo_kube_backend_encoded"'"}}' |
  podman secret create todo-kube-backend-secret -

unset todo_kube_backend_encoded

install -d -m 0700 \
  "$HOME/.config/containers/systemd/todo-kube-backend"

install -m 0644 \
  backend.yaml \
  config-lab.yaml \
  todo-kube-backend.kube \
  "$HOME/.config/containers/systemd/todo-kube-backend/"

systemctl --user daemon-reload
systemctl --user start todo-kube-backend.service
```

## Verify the real application contract

The first check verifies liveness. The disposable client then verifies
readiness and a real database read through the shared network:

```bash
systemctl --user is-active todo-kube-backend.service

podman inspect \
  --format 'Health={{.State.Health.Status}}' \
  todo-kube-backend-backend

podman run --rm \
  --network todo-network \
  --entrypoint python \
  localhost/todo-backend:m12 \
  -c 'from urllib.request import urlopen; assert urlopen("http://todo-kube-backend:8000/ready", timeout=5).status == 200'

podman run --rm \
  --network todo-network \
  --entrypoint python \
  localhost/todo-backend:m12 \
  -c 'from urllib.request import urlopen; import json; data=json.load(urlopen("http://todo-kube-backend:8000/api/todos", timeout=5)); print(f"Todo rows: {len(data)}")'

podman exec todo-kube-backend-backend \
  stat -c '%a %u:%g %n' \
  /run/secrets/todo-backend/database-password

podman logs --tail 20 todo-kube-backend-backend
```

The candidate is healthy only when both liveness and the explicit readiness
request pass. Podman Kube does not turn this application's `/ready` endpoint
into a Kubernetes readiness probe.

## Verify systemd recovery

Record the current container ID, kill the backend, and verify that systemd
recreates the candidate without affecting the accepted backend:

```bash
todo_kube_backend_old_id=$(
  podman inspect --format '{{.Id}}' todo-kube-backend-backend
)

podman kill todo-kube-backend-backend

for attempt in {1..20}
do
  if test "$(systemctl --user is-active todo-kube-backend.service)" = active &&
     podman container exists todo-kube-backend-backend &&
     test "$(podman inspect --format '{{.Id}}' todo-kube-backend-backend)" != \
       "$todo_kube_backend_old_id" &&
     test "$(podman inspect --format '{{.State.Health.Status}}' todo-kube-backend-backend)" = healthy
  then
    echo "Backend recovery: OK"
    break
  fi

  sleep 1
done

unset todo_kube_backend_old_id

systemctl --user is-active \
  todo-kube-backend.service \
  todo-backend.service

podman inspect \
  --format 'Health={{.State.Health.Status}}' \
  todo-kube-backend-backend
```

## Cleanup

The candidate has no persistent volume. Cleanup removes only its isolated
unit files, pod and translated secret:

```bash
systemctl --user stop todo-kube-backend.service

rm -f \
  "$HOME/.config/containers/systemd/todo-kube-backend/backend.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-backend/config-lab.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-backend/todo-kube-backend.kube"

rmdir "$HOME/.config/containers/systemd/todo-kube-backend"
systemctl --user daemon-reload

podman secret rm todo-kube-backend-secret

systemctl --user is-active \
  todo-postgres.service \
  todo-backend.service \
  todo-keycloak.service \
  todo-frontend.service

curl --fail http://127.0.0.1:8080/ready
echo
```

Record the completed target evidence in [RESULTS.md](RESULTS.md).
