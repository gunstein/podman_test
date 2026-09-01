# What this demo teaches

The project demonstrates important mechanisms and operational decisions without
pretending that a two-VM lab is a complete production platform. A topic is
covered when the repository either demonstrates it or states the deliberate
simplification and the normal production concern.

For a dependency-ordered walkthrough, use [LEARNING-GUIDE.md](LEARNING-GUIDE.md). For the destructive build-from-zero verification, use [LAB-ACCEPTANCE.md](LAB-ACCEPTANCE.md).

| Topic | Demonstrated here | Deliberate simplification / production concern |
|---|---|---|
| Rootless Podman | User namespaces, images, networks, volumes, ports and secrets | One service user and one application stack |
| Quadlet/systemd | Generated user services, dependencies, health, restart and lingering | No cluster-level scheduler |
| SELinux | Enforcing mode, `:Z`, `:z`, `:U`, labels and AVC troubleshooting | No custom SELinux policy module |
| fapolicyd | RPM trust, exact project-file trust and update/delete lifecycle | Manual trust administration for demo tools |
| Offline delivery | OCI archives, internal manifest and pre-extraction archive checksum | Real releases should sign artifacts with an organizational identity |
| Secrets | Local Podman secrets, direct Podman inspection, protected Ansible transfer and mismatch checks | Recovery assumes one database node survives; simultaneous loss of both nodes is outside scope |
| PostgreSQL privilege | Separate bootstrap, migrator, application, Keycloak and replication roles | One PostgreSQL cluster |
| Availability | Async physical streaming, slot health, lag and reboot recovery | One standby, no automatic HA manager and no archive-backed `restore_command`; an invalidated slot requires re-seeding |
| RPO/RTO | Operational targets and measurable local replay state | Async RPO cannot be guaranteed after abrupt loss |
| Replication security | SCRAM authentication and host firewall boundaries | WAL transport is not TLS-enforced in the trusted demo LAN |
| Fencing | Mandatory operator confirmation before promotion | VM fencing is performed in Proxmox, not automated by the app |
| Promotion | Local preflight, explicit confirmations and writable verification | No automatic failover |
| Application failover | Stable hostname, issuer, nginx and promoted app tier | Client name/IP mapping is manual |
| TLS identity during DR | Server/private-key versus client/root trust, hostname validation and explicit nginx root export | M14 creates a new local OpenSSL demo CA after promotion; production should pre-stage trust, use managed PKI/public ACME or terminate TLS at a redundant stable endpoint |
| Restore redundancy | Re-seed the old primary as the new standby | Full re-seed is preferred over `pg_rewind` for clarity |
| Backup/PITR | Base backup, continuous WAL, named restore point and isolated restore test | Backup volume is on the same VM |
| Backup operations | Archive status, safe disposable cleanup and documented growth | Off-host copy, retention, encryption and alerts are documented, not implemented |
| Observability | Health/readiness, replication/slot status and operator commands | No Prometheus, alert manager or dashboard stack |

## Lifecycle model

```text
install → normal operation → replication monitoring
        → primary failure → infrastructure fencing
        → standby preflight → promotion → application failover
        → backup/PITR verification → rebuild old primary as standby
        → replication verified → redundancy restored
```

Promotion restores availability, not redundancy. Rebuilding a standby restores
redundancy. Returning service to the machine that was originally primary is an
optional later switchover, not an automatic part of disaster recovery.

## Tool responsibilities

```text
Quadlet       describes how each container runs
systemd       owns service lifecycle and boot behavior
Ansible       provisions and verifies desired state
Python tools  perform guarded operational DR and backup workflows
Podman        provides the rootless container runtime
```

This separation is intentional. The Python tools do not become a second
configuration-management system, and Ansible does not hide dangerous promotion
or destructive recovery choices inside an ordinary deployment.
