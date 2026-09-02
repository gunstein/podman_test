# PostgreSQL Kube persistence candidate

This candidate validates a fresh PostgreSQL workload without touching the
accepted database. It has its own pod, DNS name, loopback test port, secret and
persistent volume:

- pod and DNS: `todo-kube-postgres`;
- loopback port: `127.0.0.1:15432`;
- translated Kube secret: `todo-kube-postgres-secret`; and
- data volume: `todo-kube-postgres-data`.

The candidate uses the accepted `todo-db-password` only as the authoritative
source value. It wraps that value as a Kube-compatible Podman secret entirely
through shell memory. It never mounts `todo-postgres-data` and does not change
the accepted PostgreSQL instance, replication configuration, backup volume or
DR state.

This gate proves fresh initialization, authentication, rootless UID/GID and
SELinux behavior, liveness, systemd recovery and crash-safe persistence. It
does not yet prove streaming replication, promotion, WAL archiving or PITR;
those remain separate acceptance gates.

## Install

Copy this directory to the current primary, enter it and verify both the
accepted runtime and the isolated candidate names:

```bash
podman image exists docker.io/library/postgres:17.11
podman network exists todo-network
podman secret exists todo-db-password
test "$(systemctl --user is-active todo-postgres.service)" = active

test ! -e "$HOME/.config/containers/systemd/todo-kube-postgres" || {
  echo "STOP: candidate unit directory already exists" >&2
  exit 1
}

if podman pod exists todo-kube-postgres ||
   podman volume exists todo-kube-postgres-data ||
   podman secret exists todo-kube-postgres-secret
then
  echo "STOP: candidate Podman state already exists" >&2
  exit 1
fi

ss -lnt |
  grep -E ':15432[[:space:]]' && {
    echo "STOP: candidate port 15432 is already in use" >&2
    exit 1
  }
```

Translate the existing raw Podman secret without a plaintext file:

```bash
todo_kube_postgres_password=$(
  podman secret inspect \
    --showsecret \
    --format '{{.SecretData}}' \
    todo-db-password
)

todo_kube_postgres_encoded=$(
  printf '%s' "$todo_kube_postgres_password" |
    base64 -w0
)

unset todo_kube_postgres_password

printf '%s%s%s\n' \
  '{"apiVersion":"v1","kind":"Secret","metadata":' \
  '{"name":"todo-kube-postgres-secret"},"data":{"database-password":"' \
  "$todo_kube_postgres_encoded"'"}}' |
  podman secret create todo-kube-postgres-secret -

unset todo_kube_postgres_encoded
```

Install and start the candidate:

```bash
install -d -m 0700 \
  "$HOME/.config/containers/systemd/todo-kube-postgres"

install -m 0644 \
  postgres.yaml \
  config-lab.yaml \
  todo-kube-postgres.kube \
  "$HOME/.config/containers/systemd/todo-kube-postgres/"

systemctl --user daemon-reload
systemctl --user start todo-kube-postgres.service
```

## Verify initialization and storage

Wait for liveness:

```bash
for attempt in {1..60}
do
  if podman container exists todo-kube-postgres-postgres
  then
    health=$(
      podman inspect \
        --format '{{.State.Health.Status}}' \
        todo-kube-postgres-postgres
    )
  else
    health=absent
  fi

  printf 'Attempt %02d: health=%s\n' "$attempt" "$health"
  test "$health" = healthy && break
  sleep 1
done

test "$health" = healthy &&
  echo "PostgreSQL liveness: OK"

unset health
systemctl --user --failed --no-pager
```

Authenticate over the shared network with the original raw secret. This proves
that the translated file secret initialized the expected database password:

```bash
podman run --rm \
  --network todo-network \
  --secret todo-db-password,type=env,target=PGPASSWORD \
  --entrypoint psql \
  docker.io/library/postgres:17.11 \
  --host todo-kube-postgres \
  --username todo \
  --dbname todo \
  --set ON_ERROR_STOP=1 \
  --command 'SELECT current_user, current_database(), pg_is_in_recovery();'
```

Prove that host authentication does not accept an incorrect password:

```bash
if podman run --rm \
     --network todo-network \
     --env PGPASSWORD=definitely-wrong-password \
     --entrypoint psql \
     docker.io/library/postgres:17.11 \
     --host todo-kube-postgres \
     --username todo \
     --dbname todo \
     --command 'SELECT 1;'
then
  echo "ERROR: incorrect password was accepted" >&2
  exit 1
else
  echo "Wrong password rejected: OK"
fi

podman exec todo-kube-postgres-postgres \
  grep -E '^[[:space:]]*host' \
  /var/lib/postgresql/data/pg_hba.conf
```

Verify container identity, secret permissions, port publication and volume
ownership through the Podman user namespace:

```bash
podman exec todo-kube-postgres-postgres id

podman exec todo-kube-postgres-postgres \
  stat -c '%a %u:%g %n' \
  /run/secrets/todo-kube-postgres/database-password \
  /var/lib/postgresql/data \
  /var/lib/postgresql/data/PG_VERSION

ss -lnt |
  grep -E ':15432[[:space:]]'

todo_kube_postgres_mount=$(
  podman volume inspect \
    --format '{{.Mountpoint}}' \
    todo-kube-postgres-data
)

podman unshare \
  stat -c '%a %u:%g %n' \
  "$todo_kube_postgres_mount" \
  "$todo_kube_postgres_mount/PG_VERSION"

podman unshare ls -Zd "$todo_kube_postgres_mount"
unset todo_kube_postgres_mount
```

## Verify crash recovery and persistence

Create and checkpoint an isolated marker row:

```bash
podman exec todo-kube-postgres-postgres \
  psql \
    --username todo \
    --dbname todo \
    --set ON_ERROR_STOP=1 \
    --command "
      CREATE TABLE IF NOT EXISTS kube_candidate_probe (
        marker text PRIMARY KEY
      );
      INSERT INTO kube_candidate_probe (marker)
      VALUES ('survives-container-recreation')
      ON CONFLICT (marker) DO NOTHING;
      CHECKPOINT;
    "

todo_kube_postgres_old_id=$(
  podman inspect \
    --format '{{.Id}}' \
    todo-kube-postgres-postgres
)

todo_kube_postgres_system_id_before=$(
  podman exec \
    todo-kube-postgres-postgres \
    psql \
      --username todo \
      --dbname postgres \
      --tuples-only \
      --no-align \
      --command \
        'SELECT system_identifier FROM pg_control_system();'
)

podman kill todo-kube-postgres-postgres
```

Wait for systemd to recreate a healthy pod:

```bash
for attempt in {1..60}
do
  service_state=$(
    systemctl --user show \
      todo-kube-postgres.service \
      --property=ActiveState \
      --value
  )

  if podman container exists todo-kube-postgres-postgres
  then
    todo_kube_postgres_new_id=$(
      podman inspect \
        --format '{{.Id}}' \
        todo-kube-postgres-postgres
    )
    health=$(
      podman inspect \
        --format '{{.State.Health.Status}}' \
        todo-kube-postgres-postgres
    )
  else
    todo_kube_postgres_new_id=absent
    health=absent
  fi

  printf 'Attempt %02d: service=%s container=%.12s health=%s\n' \
    "$attempt" \
    "$service_state" \
    "$todo_kube_postgres_new_id" \
    "$health"

  if test "$todo_kube_postgres_new_id" != absent &&
     test "$todo_kube_postgres_new_id" != "$todo_kube_postgres_old_id" &&
     test "$health" = healthy
  then
    break
  fi

  sleep 1
done

test "$todo_kube_postgres_new_id" != "$todo_kube_postgres_old_id" &&
  test "$health" = healthy &&
  echo "Automatic PostgreSQL recovery: OK"
```

Verify the committed row and accepted runtime:

```bash
todo_kube_postgres_system_id_after=$(
  podman exec \
    todo-kube-postgres-postgres \
    psql \
      --username todo \
      --dbname postgres \
      --tuples-only \
      --no-align \
      --command \
        'SELECT system_identifier FROM pg_control_system();'
)

test "$todo_kube_postgres_system_id_before" = \
  "$todo_kube_postgres_system_id_after" &&
  echo "Same database cluster retained: OK"

podman exec todo-kube-postgres-postgres \
  psql \
    --username todo \
    --dbname todo \
    --tuples-only \
    --no-align \
    --command \
      "SELECT marker FROM kube_candidate_probe;"

unset \
  todo_kube_postgres_old_id \
  todo_kube_postgres_new_id \
  todo_kube_postgres_system_id_before \
  todo_kube_postgres_system_id_after \
  service_state \
  health

systemctl --user is-active \
  todo-postgres.service \
  todo-backend.service \
  todo-keycloak.service \
  todo-frontend.service

systemctl --user --failed --no-pager
curl --fail http://127.0.0.1:8080/ready
echo
```

## Cleanup

Stop normally and prove that Kube teardown retains the database volume:

```bash
systemctl --user stop todo-kube-postgres.service

podman volume exists todo-kube-postgres-data &&
  echo "Database volume retained after normal stop: OK"

if podman pod exists todo-kube-postgres
then
  echo "ERROR: candidate pod still exists" >&2
else
  echo "Candidate pod removed after stop: OK"
fi
```

Remove only the explicitly named candidate state:

```bash
rm -f \
  "$HOME/.config/containers/systemd/todo-kube-postgres/postgres.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-postgres/config-lab.yaml" \
  "$HOME/.config/containers/systemd/todo-kube-postgres/todo-kube-postgres.kube"

rmdir "$HOME/.config/containers/systemd/todo-kube-postgres"
systemctl --user daemon-reload

podman volume rm todo-kube-postgres-data
podman secret rm todo-kube-postgres-secret

systemctl --user is-active \
  todo-postgres.service \
  todo-backend.service \
  todo-keycloak.service \
  todo-frontend.service

systemctl --user --failed --no-pager
curl --fail http://127.0.0.1:8080/ready
echo
```
