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

The liveness check can be forced to fail by creating a marker inside the
container. `ExecStartPost` then sets Podman's health-on-failure action to
`kill`. Together with `ExitCodePropagation=any` and systemd
`Restart=on-failure`, this must replace the failed container and return it to a
healthy state.

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

podman inspect \
  --format 'Action={{.Config.HealthcheckOnFailureAction}}' \
  todo-kube-name-contract
```

The generated `ExecStart` must contain `--no-pod-prefix`. The service must be
active, both Podman objects must resolve by the exact shared name, the container
must become healthy, and it must run as UID/GID 1000.

## Verify unhealthy recovery

```bash
old_id=$(podman inspect --format '{{.Id}}' todo-kube-name-contract)

podman exec \
  todo-kube-name-contract \
  touch /tmp/todo-kube-force-unhealthy

for attempt in {1..60}
do
  if podman container exists todo-kube-name-contract
  then
    new_id=$(podman inspect --format '{{.Id}}' todo-kube-name-contract)
    health=$(podman inspect \
      --format '{{.State.Health.Status}}' \
      todo-kube-name-contract)
  else
    new_id=absent
    health=absent
  fi

  service_state=$(systemctl --user show \
    todo-kube-name-contract.service \
    --property=ActiveState \
    --value)

  printf 'Attempt %02d: service=%s container=%.12s health=%s\n' \
    "$attempt" "$service_state" "$new_id" "$health"

  test "$new_id" != absent &&
    test "$new_id" != "$old_id" &&
    test "$health" = healthy &&
    break

  sleep 1
done

test "$new_id" != "$old_id" &&
  test "$health" = healthy &&
  echo "Health failure recovery: OK"

systemctl --user --failed --no-pager

unset old_id new_id health service_state
```

The marker lives only in the failed container, so the replacement must become
healthy without manual repair. This proves that health failure, exit-code
propagation and systemd restart form one recovery chain.

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
