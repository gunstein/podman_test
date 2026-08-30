# Secrets in the Todo demo

The demo uses two distinct layers. Podman secrets deliver credentials to
containers at runtime. They are deliberately not treated as the authoritative
backup or organization-wide source of those credentials.

```text
authoritative secret source
        |
        | deployment-time provisioning
        v
Podman secret on each required host
        |
        | file or environment delivery
        v
container process
```

M12 generates secrets on the first host because that keeps the offline lab
self-contained. M13 demonstrates that Podman secrets are host-local by copying
the required values to standby through Ansible memory and SSH. The playbook
uses `podman secret inspect --showsecret`, marks value-bearing tasks `no_log`,
creates only missing secrets and rejects mismatched existing values. It never
writes a plaintext transfer file.

That is a suitable bootstrap lesson, but the first database host should not be
the long-term secret authority for a larger installation.

The main repository and M12/M13 packages include a runnable optional path:

```bash
cp ansible/secrets.example.yml ansible/secrets.yml
# Replace every placeholder with a unique generated value.
ansible-vault encrypt ansible/secrets.yml

ansible-playbook \
  --inventory ansible/inventory.ini \
  --ask-vault-pass \
  --extra-vars @ansible/secrets.yml \
  ansible/provision-secrets.yml
```

Use the M13 inventory instead to provision both DR hosts before bootstrap. A
normal deploy or secret-sync run then verifies and reuses the values. Existing
mismatches are rejected; this playbook intentionally does not implement blind
rotation or `--replace`.

## Recommended model beyond the lab

For a moderate Ansible-managed environment, keep encrypted values in Ansible
Vault or obtain them from an external secret manager. Provision the same
required Podman secrets independently to both DR hosts. Keep the Vault password
or external-manager credential outside Git and outside the deployment bundle.

Ansible Vault protects committed or transported data at rest; after decryption,
Ansible still handles the plaintext during the run. Continue to use `no_log`,
restrict controller access and avoid debug output. A dedicated secret manager
becomes preferable when centralized audit, dynamic credentials, automatic
rotation or many teams are required.

Podman's default `file` driver is reasonable runtime storage for this demo. A
GPG-backed `pass` driver adds key-agent, boot and recovery requirements, so it
is not automatically safer operationally. Neither driver replaces an
authoritative, backed-up secret source.

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

1. Create or update the credential in its authoritative system.
2. Change the corresponding database, Keycloak or external-service credential
   with an overlap or rollback plan.
3. Provision the new Podman secret on every host that may run the workload.
4. Recreate the affected containers and verify readiness and authentication.
5. Retire the old credential only after all consumers are verified.

Automate that workflow per credential; a blind restart of the whole stack is
not a rotation strategy.

## Backup and recovery

The M15 PostgreSQL base backup and WAL archive do not back up Podman secrets,
TLS private keys, Keycloak configuration files or the Ansible secret source.
Back those up separately with access controls and restore tests. A database
restore is incomplete if the application cannot recover the credentials needed
to use it.

Never commit a plaintext secret, Vault password, private key or decrypted
variable file. See [WHAT-YOU-LEARN.md](WHAT-YOU-LEARN.md) for the demo boundary
and [the Ansible Vault guide](https://docs.ansible.com/projects/ansible/latest/vault_guide/index.html)
for the optional authoritative-source model.
