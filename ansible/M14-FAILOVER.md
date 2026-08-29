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

The current development drill started before M14 existed, so copying the package
to the already promoted standby is an explicit one-time test setup. A completed
deployment must stage it while primary is healthy.

## Host firewall

Before publishing HTTPS, allow only the intended client or management network.
For the current laptop at `192.168.0.100`, run on standby as an administrator:

```bash
sudo firewall-cmd --permanent --zone=public \
  --add-rich-rule='rule family=ipv4 source address=192.168.0.100/32 port port=8443 protocol=tcp accept'
sudo firewall-cmd --reload
```

Do not expose PostgreSQL or the internal backend and Keycloak ports. M14
publishes only HTTPS on the standby LAN address; its HTTP port remains bound to
localhost for local smoke tests.

## Deploy on the promoted standby

Verify that primary remains powered off. Extract the M14 package on standby,
copy the example inventory and set the real address:

```bash
cp ansible/inventory-m14.example.ini ansible/inventory-m14.ini
sed -i 's/192.0.2.11/192.168.0.109/' ansible/inventory-m14.ini
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
echo '192.168.0.109 todo.test' | sudo tee -a /etc/hosts
```

Copy Caddy's public root certificate from standby:

```bash
scp gunstein@192.168.0.109:.config/todo/todo-caddy-root.crt /tmp/
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

On 2026-08-29, the Oracle Linux 9.8 promoted host loaded all three staged M12
application images, started the complete application tier, exposed the stable
`todo.test` issuer and accepted an authenticated browser Todo write. A second
playbook run completed with `changed=0`. After a VM reboot, PostgreSQL, backend,
Keycloak and Caddy all returned `active`; PostgreSQL remained `f|off`, and the
LAN client verified HTTPS readiness and the replicated Todo data.
