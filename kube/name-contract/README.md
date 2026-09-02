# Stable Podman Kube database name contract

This isolated PoC verifies that Podman 5.8 can preserve an existing operational
container name when a workload moves from `.container` to `.kube`.

The service, pod and application container deliberately use the same name:

```text
todo-kube-name-contract.service
todo-kube-name-contract
todo-kube-name-contract
```

`PodmanArgs=--no-pod-prefix` passes the supported `podman kube play
--no-pod-prefix` option through Quadlet. Without that option, Podman would name
the application container `todo-kube-name-contract-todo-kube-name-contract`.

The PoC has no ports, network connectivity, secrets or persistent storage. It
uses an already available local image only to run `sleep` as a non-root process.
The manifest deliberately avoids a multiline embedded script: libmagic can
otherwise classify the complete YAML file as executable source, which a
fapolicyd policy may reject before Podman can read it.

## Install

Run on the Oracle Linux test host as the rootless Todo service user:

```bash
cd "$HOME/todo-kube-name-contract-test"

test "$(podman kube play --help | grep -c -- '--no-pod-prefix')" -eq 1

install -d -m 0700 \
  "$HOME/.config/containers/systemd/todo-kube-name-contract"

install -m 0644 \
  contract.yaml \
  todo-kube-name-contract.kube \
  "$HOME/.config/containers/systemd/todo-kube-name-contract/"

systemctl --user daemon-reload
systemctl --user start todo-kube-name-contract.service
```

## Verify

```bash
systemctl --user cat todo-kube-name-contract.service |
  grep '^ExecStart='

systemctl --user is-active todo-kube-name-contract.service

podman pod exists todo-kube-name-contract &&
  echo "Stable pod name: OK"

podman container exists todo-kube-name-contract &&
  echo "Stable container name: OK"

podman inspect \
  --format 'Name={{.Name}} Health={{.State.Health.Status}}' \
  todo-kube-name-contract

podman exec todo-kube-name-contract id
```

The generated `ExecStart` must contain `--no-pod-prefix`. The service must be
active, both Podman objects must resolve by the exact shared name, the container
must become healthy, and it must run as UID/GID 1000.

## Clean up

```bash
systemctl --user stop todo-kube-name-contract.service

rm -f \
  "$HOME/.config/containers/systemd/todo-kube-name-contract/contract.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-name-contract/todo-kube-name-contract.kube"

rmdir \
  "$HOME/.config/containers/systemd/todo-kube-name-contract"

systemctl --user daemon-reload

podman pod exists todo-kube-name-contract ||
  echo "Contract pod removed: OK"

podman container exists todo-kube-name-contract ||
  echo "Contract container removed: OK"
```
