# Secrets in the Todo demo

The demo uses Podman secrets as its single secret mechanism.

```text
initial deployment
        |
        v
Podman secrets on the initial primary
        |
        | protected Ansible transfer over SSH
        v
matching Podman secrets on the standby
        |
        v
container processes
```

The initial deployment generates independent credentials for PostgreSQL,
application roles and Keycloak. Standby bootstrap demonstrates that Podman
secrets are host-local by copying the required values from the initial primary
through Ansible memory and SSH. The playbook uses
`podman secret inspect --showsecret`, marks value-bearing tasks `no_log`, creates
only missing secrets and rejects mismatched existing values. It never writes a
plaintext transfer file.

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
3. Provision the new Podman secret on every host that may run the workload.
4. Recreate the affected containers and verify readiness and authentication.
5. Retire the old credential only after all consumers are verified.

Automate that workflow per credential; a blind restart of the whole stack is
not a rotation strategy.

## Recovery boundary

The demonstrated M16 procedure re-seeds the recoverable, fenced old primary;
that host already retains its Podman secrets. If a failed host is physically
lost, the equivalent procedure is to provision a fresh rootless Podman host,
transfer the required Podman secrets from the surviving primary and bootstrap a
new physical standby. That fresh-host replacement path is not automated here.
Simultaneous loss of both database nodes is explicitly outside this demo scope.

The M15 PostgreSQL base backup and WAL archive do not contain Podman secrets or
TLS private keys. An organization that wants recovery after loss of every node
must design separate protected secret and key recovery; that mechanism is not
implemented here.

Never commit a plaintext secret or private key. See
[WHAT-YOU-LEARN.md](WHAT-YOU-LEARN.md) for the complete demo boundary.
