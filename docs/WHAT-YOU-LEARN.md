# What this demo teaches

The project demonstrates important mechanisms and operational decisions without
pretending that a two-VM lab is a complete production platform. A topic is
covered when the repository either demonstrates it or states the deliberate
simplification and the normal production concern.

For a dependency-ordered walkthrough, use [LEARNING-GUIDE.md](LEARNING-GUIDE.md). For the destructive build-from-zero verification, use [ACCEPTANCE.md](ACCEPTANCE.md).

The current implementation uses grouped Podman Kube workloads. Kube YAML defines pod contents,
`.kube` Quadlet connects each workload to Podman and systemd, and systemd owns
its lifecycle. See [`deploy/runtime/README.md`](../deploy/runtime/README.md).

| Topic | Demonstrated here | Deliberate simplification / production concern |
|---|---|---|
| Jinja2 / Kube YAML | Build-time rendering, package/render consistency checks and seven lifecycle-grouped pods | Podman workload format, not Kubernetes orchestration; rendering never runs on targets |
| Target values | Rendered files carry `${TARGET_*}` placeholders for the public hostnames and the host's address; the installer and the DR tools fill them in with the standard library, the standby with the primary's hostnames | Only the values that differ between hosts are placeholders; everything else is fixed at build time |
| Identity adapter | Both UIs use auth.js; the Keycloak SDK is isolated in keycloak-adapter.js; each backend validates issuer/JWKS/audience | Only Keycloak is implemented; changing IdP still requires configuration and integration tests |
| Acceptance | Explicit operator gates, checksums, idempotence, trusted browser and full DR evidence, run step by step through `acceptance.py` ([current verdict](../PROJECT.md#acceptance)) | Revision-specific acceptance; no automatic full DR controller |
| Rootless Podman | User namespaces, images, networks, volumes, ports and secrets | One service user and one application stack |
| Quadlet/systemd | Generated user services, dependencies, health, restart and lingering | No cluster-level scheduler |
| SELinux | Enforcing mode, `:Z`, `:z`, `:U`, labels and AVC troubleshooting | No custom SELinux policy module |
| fapolicyd | RPM trust, exact project-file trust and update/delete lifecycle | app-ops refreshes exact path, size and SHA-256 trust with bounded polling; no blanket directory trust |
| Offline delivery | OCI archives, internal manifest and pre-extraction archive checksum | Real releases should sign artifacts with an organizational identity |
| Secrets | Local Podman secrets, direct Podman inspection, protected app-ops transfer and mismatch checks | Recovery assumes one database node survives; simultaneous loss of both nodes is outside scope |
| PostgreSQL privilege | Separate bootstrap, migrator, application, Keycloak and replication roles | One PostgreSQL server per app, no shared cluster |
| Availability | Async physical streaming, slot health, lag and reboot recovery | One standby, no automatic HA manager and no archive-backed `restore_command`; an invalidated slot requires re-seeding |
| RPO/RTO | Operational targets and measurable local replay state | Async RPO cannot be guaranteed after abrupt loss |
| Replication security | SCRAM authentication, host firewall boundaries and TLS with `verify-full` | The replication CA lives on the hosts; no managed PKI |
| Fencing | Mandatory operator confirmation before promotion | VM fencing is performed in Proxmox, not automated by the app |
| Promotion | Local preflight, explicit confirmations and writable verification | No automatic failover |
| Application failover | Stable hostname, issuer, nginx and promoted app tier | Client name/IP mapping is manual |
| TLS identity during DR | Server/private-key versus client/root trust, hostname validation and explicit nginx root export | Promoted application recovery creates a new local OpenSSL demo CA; production should pre-stage trust, use managed PKI/public ACME or terminate TLS at a redundant stable endpoint |
| Restore redundancy | Re-seed the old primary as the new standby | Full re-seed is preferred over `pg_rewind` for clarity |
| Backup/PITR | Nightly verified base backups on every server install with a restore of the latest one; on a DR primary also continuous WAL and an isolated restore test to a named restore point or to a time | Backup volume is on the same VM; a single host restores to last night only |
| Backup operations | Archive status, safe disposable cleanup, 7 days' retention with WAL pruning, and a crash-safe WAL archive | Off-host copy, encryption and regular restore tests are documented, not implemented |
| Observability | Health/readiness, replication/slot status, operator commands, and systemd timers that turn a stopped replication, a failing archive or backup, or a filling disk into a failed unit | No Prometheus, alert manager or dashboard stack; nobody is paged |

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
Jinja2        renders workload YAML at build time; absent from target hosts
Kube YAML     defines seven workloads, init containers and runtime settings
.kube Quadlet connects each workload to user systemd
systemd       owns service lifecycle and boot behavior, and runs the nightly
              backup and the DR check from user timers
app-ops       provisions and verifies desired state on the DR pair
Python tools  perform guarded operational DR and backup workflows
Podman        provides the rootless container runtime
```

This separation is intentional. The Python tools do not become a second
configuration-management system, and app-ops does not hide dangerous promotion
or destructive recovery choices inside an ordinary deployment.
