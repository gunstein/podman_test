# Podman Kube lifecycle PoC

This PoC tests the smallest useful part of the proposed runtime before any Todo
service is migrated.

It answers these questions on the supported Oracle Linux host:

1. Can two independent pods communicate by stable DNS name on one rootless
   user-defined Podman network?
2. Can a pod consume an externally provisioned Podman secret without secret
   data in Git?
3. Does a named volume remain usable by a non-root container?
4. Can the same pod YAML run directly with `podman kube play` and through a
   `.kube` Quadlet?
5. Does a container failure propagate to systemd when
   `ExitCodePropagation=any` is selected?
6. Does `podman kube play --wait` clean up the development workload when it is
   interrupted?
7. Are container logs available through Podman's journald log driver after
   `.kube` startup and reboot?

The PoC uses the existing `localhost/todo-backend:m12` image only to avoid a new
image dependency. It does not connect to the Todo database or alter the running
Todo application.

## Important secret representation detail

`podman kube play` expects a referenced Podman named secret to contain a whole
Kubernetes Secret document. A raw Podman secret containing only a password is
not interchangeable with this representation.

The test therefore creates the isolated secret `todo-kube-poc-secret`. Its
value is read interactively, base64 encoded and wrapped in a Kubernetes Secret
document in memory before it is sent to `podman secret create`. No secret value
or generated secret file is stored in this repository.

This is an explicit acceptance question, not yet the final Todo secret design.

## Resource names

Every resource is prefixed with `todo-kube-poc-`:

- network: `todo-kube-poc-network`
- secret: `todo-kube-poc-secret`
- volume: `todo-kube-poc-data`
- pods: `todo-kube-poc-server` and `todo-kube-poc-consumer`

Do not run this PoC if those names already belong to another workload.

## Direct development test

Run from this directory as the rootless service user. First verify the
prerequisites and create the external inputs:

```bash
podman --version
podman kube play --help | grep -- --wait
test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
test "$(podman info --format '{{.Host.CgroupsVersion}}')" = v2
podman image exists localhost/todo-backend:m12 || {
  echo "STOP: localhost/todo-backend:m12 is missing" >&2
  exit 1
}

for resource in \
  todo-kube-poc-server \
  todo-kube-poc-consumer
do
  test -z "$(podman pod ps --filter name=^${resource}$ --quiet)" || {
    echo "STOP: pod already exists: $resource" >&2
    exit 1
  }
done

if podman network exists todo-kube-poc-network ||
   podman secret exists todo-kube-poc-secret ||
   podman volume exists todo-kube-poc-data
then
  echo "STOP: a non-pod PoC resource already exists" >&2
  exit 1
fi

podman network create todo-kube-poc-network

read -rsp "Temporary PoC secret: " todo_kube_poc_value
echo

todo_kube_poc_encoded=$(
  printf '%s' "$todo_kube_poc_value" |
    base64 -w0
)

unset todo_kube_poc_value

printf '%s\n' \
  '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"todo-kube-poc-secret"},"data":{"token":"'"$todo_kube_poc_encoded"'"}}' |
  podman secret create todo-kube-poc-secret -

unset todo_kube_poc_encoded

podman volume create todo-kube-poc-data
```

Start the server in terminal A:

```bash
podman kube play \
  --network todo-kube-poc-network \
  --wait \
  server.yaml
```

Start the consumer in terminal B:

```bash
podman kube play \
  --network todo-kube-poc-network \
  consumer.yaml
```

Verify DNS, HTTP, secret delivery and persistent-volume access:

```bash
podman exec todo-kube-poc-consumer-consumer \
  getent hosts todo-kube-poc-server

podman exec todo-kube-poc-consumer-consumer \
  python -c \
  'from urllib.request import urlopen; assert urlopen("http://todo-kube-poc-server:8000/health", timeout=5).status == 200'

podman exec todo-kube-poc-consumer-consumer \
  test -s /run/secrets/todo-kube-poc/token

podman exec todo-kube-poc-consumer-consumer \
  stat -c '%a %u:%g %n' /run/secrets/todo-kube-poc/token

podman exec todo-kube-poc-consumer-consumer \
  sh -c 'printf "%s\n" kube-poc-ok > /data/result'

podman run --rm \
  --volume todo-kube-poc-data:/data:ro,z \
  --entrypoint cat \
  localhost/todo-backend:m12 \
  /data/result

todo_kube_poc_mount=$(
  podman volume inspect \
    --format '{{.Mountpoint}}' \
    todo-kube-poc-data
)

ls -Zd "$todo_kube_poc_mount"
unset todo_kube_poc_mount
```

The secret-content check is intentionally non-disclosing: it tests only that
the file is non-empty. On an SELinux-enforcing target, the named volume must
have a container-usable label and the write must complete without an AVC.

Stop terminal A with `Ctrl-C`. Because it used `--wait`, the server pod should
be removed. Remove the consumer with:

```bash
podman kube play --down consumer.yaml

test -z "$(podman pod ps --filter name=^todo-kube-poc-server$ --quiet)"
test -z "$(podman pod ps --filter name=^todo-kube-poc-consumer$ --quiet)"
podman volume exists todo-kube-poc-data
```

The final command must succeed: normal pod teardown must not delete persistent
data.

## `.kube` Quadlet test

Do not run the direct-development and systemd tests at the same time. Install
the PoC files together so relative `Yaml=` references remain deterministic:

```bash
install -d -m 0700 "$HOME/.config/containers/systemd/todo-kube-poc"

install -m 0644 \
  server.yaml \
  consumer.yaml \
  todo-kube-poc.network \
  todo-kube-poc-server.kube \
  todo-kube-poc-consumer.kube \
  "$HOME/.config/containers/systemd/todo-kube-poc/"

systemctl --user daemon-reload
systemctl --user start todo-kube-poc-consumer.service

systemctl --user is-active \
  todo-kube-poc-server.service \
  todo-kube-poc-consumer.service
```

Repeat the DNS, HTTP, secret and volume checks from the previous section. Then
inspect the generated lifecycle rather than assuming it:

```bash
systemctl --user status todo-kube-poc-server.service --no-pager
systemctl --user status todo-kube-poc-consumer.service --no-pager
systemctl --user cat todo-kube-poc-consumer.service

podman pod inspect todo-kube-poc-server
podman pod inspect todo-kube-poc-consumer
```

The crash-propagation experiment is deliberately left as a separate manual
step after basic startup succeeds. Kill the consumer's main process, then
record the container and systemd states:

```bash
podman kill todo-kube-poc-consumer-consumer
sleep 2

podman ps -a --filter pod=todo-kube-poc-consumer
systemctl --user is-failed todo-kube-poc-consumer.service
journalctl --user -u todo-kube-poc-consumer.service -n 50 --no-pager
```

The desired result is that systemd observes the failure. If the container is
silently restarted inside the pod or the unit remains healthy, this lifecycle
model has not passed the PoC.

## Logging

The `.kube` units select `LogDriver=journald`. Verify both the configured
driver and the normal rootless operator interface:

```bash
podman inspect \
  --format 'LogDriver={{.HostConfig.LogConfig.Type}}' \
  todo-kube-poc-server-server

podman logs --tail 10 todo-kube-poc-server-server
```

On the tested Oracle Linux host, container records are stored in the system
journal. The service user can read them through `podman logs`, while a direct
journal query requires journal access:

```bash
sudo journalctl \
  CONTAINER_NAME=todo-kube-poc-server-server \
  -n 10 \
  --no-pager
```

`journalctl --user -u todo-kube-poc-server.service` is not the container-log
interface in this setup. The user unit owns the Kube lifecycle, while Podman's
journald driver records container stdout and stderr using container metadata.

## Cleanup

Cleanup is intentionally explicit and limited to PoC-prefixed resources:

```bash
systemctl --user stop \
  todo-kube-poc-consumer.service \
  todo-kube-poc-server.service || true

rm -f \
  "$HOME/.config/containers/systemd/todo-kube-poc/server.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-poc/consumer.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-poc/todo-kube-poc.network" \
  "$HOME/.config/containers/systemd/todo-kube-poc/todo-kube-poc-server.kube" \
  "$HOME/.config/containers/systemd/todo-kube-poc/todo-kube-poc-consumer.kube"

rmdir "$HOME/.config/containers/systemd/todo-kube-poc" || true

systemctl --user daemon-reload

podman kube play --down consumer.yaml || true
podman kube play --down server.yaml || true
podman pod rm --force todo-kube-poc-consumer todo-kube-poc-server || true
podman network rm todo-kube-poc-network || true
podman secret rm todo-kube-poc-secret || true
podman volume rm todo-kube-poc-data || true
```

The persistent volume is removed only by the final explicit command.

## Version-specific expectations

The target is the Oracle Linux Podman version used by the acceptance lab. The
PoC must record the exact `podman --version` output.

Podman 5.8.2 documents `--wait`, `.kube` Quadlets, external named Kube secrets,
custom networks and `ExitCodePropagation`. It does not expose a
`--validate=strict` option for `podman kube play`; runtime creation in a
disposable namespace is therefore the validation method for this PoC.

The manifests intentionally limit memory but not CPU. On the tested Oracle
Linux host, the rootless user manager was delegated only the `memory` and
`pids` cgroup v2 controllers. Adding a Kubernetes CPU limit made `crun` reject
the workload because the `cpu` controller was unavailable. The PoC does not
change host-wide cgroup delegation merely to make that optional limit work.
Production CPU governance must be designed explicitly, either through
administrator-controlled systemd slices or tested controller delegation.

See [RESULTS.md](RESULTS.md) for test evidence and remaining gates.
