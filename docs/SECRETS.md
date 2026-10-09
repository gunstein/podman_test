# Secrets in the Todo demo

The demo uses Podman secrets as its single secret mechanism.

```text
initial deployment
        |
        v
Podman secrets on the initial primary
        |
        | protected app-ops transfer over SSH
        v
matching Podman secrets on the standby
        |
        v
container processes
```

The initial deployment generates independent credentials for PostgreSQL,
application roles and Keycloak. Standby bootstrap demonstrates that Podman
secrets are host-local by copying the required values from the initial primary
through app-ops memory and SSH. `app_installer` owns the transfer: its export
command reads the complete group with `podman secret inspect --showsecret`, and
its import command checks every standby secret before creating only the missing
ones. A mismatched existing value stops the import before any secret is created.
app-ops pipes the value from one command's stdout to the other's stdin, never
logs it and never writes a plaintext transfer file. The same copy carries the
replication CA (`replication-ca-key`, `replication-ca-cert`) that protects the
WAL stream with TLS; a promoted host does not need it to run the application,
only to serve replication again.

After promotion, the surviving node holds the values needed to rebuild the
other node. Destructive rebuild preflight compares the replication secret on
both hosts and authenticates a replication connection before deleting old
database data.

Podman's default `file` driver is reasonable runtime storage for this demo. A
GPG-backed `pass` driver adds key-agent, boot and recovery requirements, so it
is not automatically simpler or safer operationally.

## Runtime delivery

Prefer a mounted secret file when the application supports a `*_FILE` setting.
The backend and PostgreSQL-related jobs use this pattern, so credentials do not
become ordinary container environment variables.

Keycloak accepts its bootstrap and database credentials through environment
variables, so its Quadlet maps Podman secrets to environment variables. This is
an interface constraint, not the preferred default for new application code.

Public certificates are not secrets. TLS private keys and CA private keys are.

nginx's TLS files are the worked example of files as Podman secrets
([TLS](TLS.md#nginxs-tls-files-as-podman-secrets)): one raw secret per file
(`todo-proxy-*`), made in throwaway containers that print a key straight
into `podman secret create NAME -`, mounted into other throwaway containers
with `--secret NAME,type=mount,target=FILE`, and given to nginx as the Kube
secret `todo-kube-proxy-tls-secret`, which `podman kube play` mounts as a
read-only directory. They are host-local: the DR copy never carries them,
and each host makes its own key. The demo CA's key and a waiting key are
never in nginx's Kube secret.
For an organization-PKI variant, deploy the public certificate as a reviewed
configuration file and deliver the node's private key as a Podman secret. Do
not copy an organization root private key to application hosts.

## Rotation

Replacing a Podman secret does not update an already-created container. A
controlled rotation therefore needs this order:

1. Create or update the credential on the active node with an explicit
   rollback plan.
2. Change the corresponding database, Keycloak or external-service credential
   with an overlap or rollback plan.
3. Provision the new Podman secret on every host that may run the workload,
   and remove the Kube secret made from it (named `*-kube-*`). The next
   install creates it again from the new value; while the two differ, the
   installer stops and names the Kube secret.
4. Recreate the affected containers and verify readiness and authentication.
5. Retire the old credential only after all consumers are verified.

Automate that workflow per credential; a blind restart of the whole stack is
not a rotation strategy.

## Recovery boundary

The demonstrated standby-rebuild procedure re-seeds the recoverable, fenced old primary;
that host already retains its Podman secrets. If a failed host is physically
lost, the equivalent procedure is to provision a fresh rootless Podman host,
transfer the required Podman secrets from the surviving primary and bootstrap a
new physical standby. That fresh-host replacement path is not automated here.
Simultaneous loss of both database nodes is explicitly outside this demo scope.

The PostgreSQL base backup and WAL archive do not contain Podman secrets or
TLS private keys. An organization that wants recovery after loss of every node
must design separate protected secret and key recovery; that mechanism is not
implemented here.

Never commit a plaintext secret or private key. See
[WHAT-YOU-LEARN.md](WHAT-YOU-LEARN.md) for the complete demo boundary.
