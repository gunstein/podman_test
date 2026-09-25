# PostgreSQL standby bootstrap

The initial bootstrap creates the replication credential and role, publishes
primary PostgreSQL on the LAN interface, takes one streamed base backup and
starts standby in recovery mode. It is deliberately separate from normal
deployment, and does not install the local DR tool: that keeps controller-side
`fapolicyd` source checks ahead of the one-shot database work.

## Firewall and preflight contract

Bootstrap requires a firewalld rich rule on the primary, allowing only standby
to reach ports 5432 (Todo), 5433 (Notes) and 5434 (Keycloak); never open these
ports to the entire LAN. Rootless Podman port forwarding does not preserve the
original client source address. The primary role therefore inspects
`app-network` and grants the dedicated replication role access from that
internal Podman subnet instead. In the verified Oracle Linux environment
PostgreSQL saw `10.89.0.0/24`, not the standby LAN address; firewalld enforces
the real machine boundary.

The read-only preflight queries firewalld to confirm that rule is already in
place before bootstrap runs, so it needs become privileges. It also requires
the offline bundle already staged on standby, by default under
`/home/<ansible_user>/todo-offline-m12` (set `todo_user_home` in the inventory
for another remote home directory).

Before that, each host reports itself with `app_installer node-facts` and
stops if its hostname, address or machine ID does not match the inventory.
`app_installer check-standby-pair` then compares both reports before anything
changes: roles, distinct names, machine IDs and addresses, every primary data
volume present and no standby data volume present. It reports every problem
at once.

The demo authenticates replication with SCRAM-SHA-256 but does not configure or
require encrypted PostgreSQL transport. It is intended for this isolated,
trusted demo LAN. A networked deployment should add PostgreSQL TLS with
`hostssl` and `sslmode=verify-full`, or use a separately protected replication
network, before treating WAL traffic as confidential.

## Bootstrap and replication-status contract

Use [the standby bootstrap phase](../../docs/ACCEPTANCE.md#4-initial-standby-bootstrap)
for the exact firewall rule, preflight, `bootstrap-standby.yml` and
`replication-status.yml` sequence.

`bootstrap-standby.yml` verifies connectivity before creating the standby
volume, and is a one-time operation that refuses to overwrite an existing
standby volume. If it fails after creating the volume or physical slot, do not
rerun it blindly: inspect the partial state first. Dropping the slot or
deleting the volume is an explicit destructive recovery operation, never an
automatic playbook cleanup.

`replication-status.yml` requires `streaming|async` on primary and recovery
`t` on standby, plus an active, usable `todo_standby` slot; it also reports WAL
status, remaining safe WAL bytes and any invalidation reason.

After replication is healthy, use
[the local DR tool phase](../../docs/ACCEPTANCE.md#5-local-dr-tool) and
[the controlled promotion runbook](PROMOTION.md) to install or update the DR
tool on standby without touching the database.

The Kube-native standby stores a `0600` replication passfile and recovery
settings inside each `0700` database volume. The canonical PostgreSQL YAML and
`.kube` unit of each database (for example `postgres.yaml` and
`todo-postgres.kube`) are shared with primary; only data/recovery configuration
and the primary LAN port exposure differ by role.

A physical slot retains WAL while standby is disconnected, capped at
1 GiB to protect primary disk space. Exceeding the cap may invalidate the slot
and require an explicit standby rebuild. The safe disconnection period is
therefore determined by WAL volume, not elapsed days: a quiet database may stay
recoverable for days, while a busy database may consume the allowance quickly.

This demo standby receives WAL only through streaming replication and does not
use the backup WAL archive as a fallback source. A more resilient design can retain WAL
off-host and configure standby `restore_command` to retrieve an older segment
that primary no longer retains before streaming resumes. That reduces avoidable
rebuilds but adds archive availability, retention, access-control and monitoring
requirements. For clarity and primary-disk safety, this demo uses an explicit
new `pg_basebackup` when the slot can no longer supply the missing WAL.

The standby PostgreSQL `.kube` Quadlet is attached to the user `default.target`; enable
lingering for the service user when it must restart at VM boot without an
interactive login.

For inherited Podman lock state after cloning, use the
[troubleshooting reference](../../docs/ACCEPTANCE-TROUBLESHOOTING.md#rootless-podman-lock-state-after-cloning).

## Acceptance evidence

Use [Acceptance](../../docs/ACCEPTANCE.md) for the full sequence and verdict.
[688a0f6](../../docs/history/ACCEPTANCE-688a0f6.md) records historical evidence only.
