# M14 application failover

M14 starts the application tier on an already promoted PostgreSQL standby. It
does not initialize PostgreSQL, run migrations, change database roles or create
secrets.

The stable demo identity is:

```text
https://todo.test:8443
```

The same name must be used by Caddy, Keycloak, the backend issuer check and the
Keycloak frontend client. During this two-VM LAN drill, the client maps the name
to the promoted host address.

## Stage before an incident

Keep the M14 source package and the M12 offline image bundle on standby before
an incident. The package contains no secrets, images or inventory.

Build, verify and transfer the package from the trusted source host while
primary is healthy:

```bash
scripts/build-m14-test-package.sh
(
  cd dist
  sha256sum -c todo-m14-test.tar.gz.sha256
)
read -rp "Standby IPv4 address: " TODO_STANDBY_IP
scp \
  dist/todo-m14-test.tar.gz \
  dist/todo-m14-test.tar.gz.sha256 \
  "gunstein@${TODO_STANDBY_IP}:"
```

On standby, verify and stage it before an incident:

```bash
cd "$HOME"
sha256sum -c todo-m14-test.tar.gz.sha256
mkdir -p todo-m14-test
tar -xzf todo-m14-test.tar.gz \
  --strip-components=1 \
  --directory todo-m14-test
```

## Host firewall

Before publishing HTTPS, allow only the intended client or management network.
On standby, enter the client IPv4 address that should be allowed:

```bash
read -rp "Allowed client IPv4 address: " TODO_CLIENT_IP
todo_firewall_rule="rule family=ipv4 \
source address=${TODO_CLIENT_IP}/32 \
port port=8443 protocol=tcp accept"
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule="$todo_firewall_rule"
sudo firewall-cmd --reload
```

Do not expose PostgreSQL or the internal backend and Keycloak ports. M14
publishes only HTTPS on the standby LAN address; its HTTP port remains bound to
localhost for local smoke tests.

## Deploy on the promoted standby

Verify that primary remains powered off. On the promoted standby:

```bash
cd "$HOME/todo-m14-test"
read -rp "Promoted host IPv4 address: " TODO_STANDBY_IP
cp ansible/inventory-m14.example.ini ansible/inventory-m14.ini
sed -i "s/192.0.2.11/${TODO_STANDBY_IP}/" ansible/inventory-m14.ini
```

Then run:

```bash
ansible-playbook \
  --inventory ansible/inventory-m14.ini \
  ansible/deploy-promoted-m14.yml
```

The playbook fails before changing application state unless:

- it runs on the declared host and address;
- `todo-postgres.service` is active;
- PostgreSQL reports `f|off`, meaning promoted and writable;
- all three runtime secrets exist;
- all three M12 application image archives exist.

It loads only missing images, installs dedicated promoted-host Quadlets, starts
Keycloak, backend and Caddy, updates the existing Keycloak client to the stable
origin, and checks health, readiness, discovery and public Todo reads.

## Client name and certificate

On the laptop, add the temporary LAN mapping:

```bash
read -rp "Promoted host IPv4 address: " TODO_STANDBY_IP
printf '%s %s\n' "$TODO_STANDBY_IP" todo.test | sudo tee -a /etc/hosts
```

Copy Caddy's public root certificate from standby:

```bash
read -rp "Promoted host IPv4 address: " TODO_STANDBY_IP
scp \
  "gunstein@${TODO_STANDBY_IP}:.config/todo/todo-caddy-root.crt" \
  /tmp/
sudo cp /tmp/todo-caddy-root.crt /usr/local/share/ca-certificates/todo-m14.crt
sudo update-ca-certificates
```

Then open <https://todo.test:8443/>. The private CA key remains in the
`todo-caddy-data` Podman volume; only the public root certificate is copied.

## Verify locally on standby

```bash
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
curl --fail http://127.0.0.1:8080/api/todos

curl --fail \
  http://127.0.0.1:8080/auth/realms/todo/.well-known/openid-configuration
```

The discovery document issuer must be
`https://todo.test:8443/auth/realms/todo`.

The old primary must remain fenced. Rejoining or failing back is a separate
operation that starts by rebuilding it as a replica of the promoted database.

## Verified drill

On 2026-08-29, the Oracle Linux 9.8 drill used promoted host
`192.168.0.109` and client `192.168.0.100`. The promoted host loaded all three
staged M12 application images, started the complete application tier and
exposed the stable `todo.test` issuer. It accepted an authenticated browser
Todo write. A second playbook run completed with `changed=0`. After a VM
reboot, PostgreSQL, backend, Keycloak and Caddy all returned `active`;
PostgreSQL remained `f|off`, and the LAN client verified HTTPS readiness and
the replicated Todo data.
