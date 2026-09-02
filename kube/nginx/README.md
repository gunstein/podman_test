# nginx Kube candidate

This candidate runs the real Todo frontend and reverse proxy in parallel with
the accepted nginx service. It uses the accepted backend and Keycloak as its
upstreams and publishes only isolated loopback test ports:

- HTTP: `127.0.0.1:18080`
- HTTPS: `127.0.0.1:18443`

Non-secret environment and nginx configuration are external ConfigMaps. The
manifest declares the persistent `todo-kube-nginx-data` volume, including the
UID/GID 101 ownership required by the non-root nginx image. Normal Kube teardown
must retain this TLS state.

## Install

Copy this directory to the current primary, enter it and verify the accepted
runtime first:

```bash
podman image exists localhost/todo-frontend:m12
podman network exists todo-network
test "$(systemctl --user is-active todo-backend.service)" = active
test "$(systemctl --user is-active todo-keycloak.service)" = active

test ! -e "$HOME/.config/containers/systemd/todo-kube-nginx" || {
  echo "STOP: candidate unit directory already exists" >&2
  exit 1
}

if podman pod exists todo-kube-nginx ||
   podman volume exists todo-kube-nginx-data
then
  echo "STOP: candidate Podman state already exists" >&2
  exit 1
fi

install -d -m 0700 \
  "$HOME/.config/containers/systemd/todo-kube-nginx"

install -m 0644 \
  nginx.yaml \
  config-lab.yaml \
  todo-kube-nginx.kube \
  "$HOME/.config/containers/systemd/todo-kube-nginx/"

systemctl --user daemon-reload
systemctl --user start todo-kube-nginx.service
```

## Verify

Wait for health, then verify HTTP routing and TLS using the generated CA:

```bash
for attempt in {1..30}
do
  health=$(
    podman inspect \
      --format '{{.State.Health.Status}}' \
      todo-kube-nginx-nginx
  )

  printf 'Health: %s\n' "$health"
  test "$health" = healthy && break
  sleep 1
done

unset health

curl --fail http://127.0.0.1:18080/health
echo
curl --fail http://127.0.0.1:18080/ready
echo

podman cp \
  todo-kube-nginx-nginx:/var/lib/todo-tls/ca.crt \
  /tmp/todo-kube-nginx-ca.crt

chmod 0644 /tmp/todo-kube-nginx-ca.crt

curl --noproxy '*' \
  --resolve todo.test:18443:127.0.0.1 \
  --cacert /tmp/todo-kube-nginx-ca.crt \
  --fail \
  https://todo.test:18443/ready

echo

curl --noproxy '*' \
  --resolve todo.test:18443:127.0.0.1 \
  --cacert /tmp/todo-kube-nginx-ca.crt \
  --fail \
  https://todo.test:18443/auth/realms/todo/.well-known/openid-configuration |
  python3 -m json.tool |
  grep '"issuer"'

podman exec todo-kube-nginx-nginx \
  openssl x509 \
    -in /var/lib/todo-tls/server.crt \
    -noout \
    -checkhost todo.test

podman exec todo-kube-nginx-nginx \
  stat -c '%a %u:%g %n' \
  /var/lib/todo-tls/ca.key \
  /var/lib/todo-tls/server.key
```

Inspect the named volume on the SELinux host:

```bash
todo_kube_nginx_mount=$(
  podman volume inspect \
    --format '{{.Mountpoint}}' \
    todo-kube-nginx-data
)

ls -Zd "$todo_kube_nginx_mount"
ls -Z "$todo_kube_nginx_mount"
unset todo_kube_nginx_mount
```

## Cleanup

Stop the candidate normally and prove that its TLS volume remains before
explicitly deleting it:

```bash
systemctl --user stop todo-kube-nginx.service

podman volume exists todo-kube-nginx-data &&
  echo "TLS volume retained after normal stop: OK"

rm -f \
  "$HOME/.config/containers/systemd/todo-kube-nginx/nginx.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-nginx/config-lab.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-nginx/todo-kube-nginx.kube"

rmdir "$HOME/.config/containers/systemd/todo-kube-nginx"
systemctl --user daemon-reload

podman volume rm todo-kube-nginx-data
rm -f /tmp/todo-kube-nginx-ca.crt

systemctl --user is-active \
  todo-postgres.service \
  todo-backend.service \
  todo-keycloak.service \
  todo-frontend.service

curl --fail http://127.0.0.1:8080/ready
echo
```
