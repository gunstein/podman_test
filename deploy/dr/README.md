# app-ops: DR operations over plain SSH

For an incident, start with the one-page [runbooks](../../docs/runbooks/README.md);
this page is the reference behind them.

`app-ops` runs the DR and multi-host operations over plain `ssh` from the
controller. It is the only DR tool: the Ansible playbooks it replaced were
retired after its CLEAN PASS on `2165933`
([PROJECT.md](../../PROJECT.md#acceptance)); Git history keeps them.

Everything DR lives here, apart from the single-host installer it builds on:

| Where | What | Runs on |
|---|---|---|
| `app_ops/` | `python3 -m app_ops`: one command per DR operation, over SSH | the controller |
| `app_dr_host/` | `python3 -m app_dr_host`: the building blocks app-ops runs on each host (replication, replication TLS, nginx's certificate from your CA, reseed, promoted deploy, secret transfer, pair checks) | each host, staged by app-ops |
| `scripts/` | `app_dr.py` (promotion), `app_backup.py` (backup and PITR), `app-quarantine.sh`, `bootstrap-ssh-key.sh` | the hosts (`/opt/todo/bin`), the controller for the last |

## Where DR finds the installer

DR reuses the single-host installer instead of copying it: `app_installer`
(deploy/installer) owns the app registry, the workloads, the secrets and the
Quadlet files. DR imports the installer; the installer never imports DR
(`tests/test_dr_boundary.py`). Where the two packages are depends on where
the code runs:

| Code | Runs on | Finds `app_installer` (and `app_dr_host`) |
|---|---|---|
| `app_ops` | the controller, from a checkout or the operations package | `deploy/installer` in the same tree, added by `app_ops/__init__.py` |
| `app_dr_host` | each host | `PYTHONPATH`, which app-ops sets to the directory it staged both packages in: `/opt/todo/lib` when fapolicyd is active |
| `app_dr.py`, `app_backup.py` | each host, in `/opt/todo/bin` | `/opt/todo/lib`, the `lib` next to their own `bin`; in a checkout, set `PYTHONPATH=deploy/installer:deploy/dr` |
| `app-quarantine.sh` | the old primary | `PYTHONPATH=/opt/todo/lib` in the script |

`tests/test_operations_distribution.py` unpacks the operations package, lays
it out as app-ops does on a host, and starts every entry point, so a broken
path fails in CI rather than in the lab.

These operations protect one group of three databases: Todo, Notes and the
shared Keycloak database (`apps.REPLICATED_DATABASES`). Each has its own host
replication port (5432, 5433, 5434), slot, credential, WAL archive and backup
volume. Every command refuses a partial group. Promoted application recovery
deploys both apps, Keycloak and the shared proxy.

Single-host installation and removal use the
[Python installer](../installer/README.md) directly, with no DR tool:

```bash
PYTHONPATH=deploy/installer python3 -m app_installer install --mode server
PYTHONPATH=deploy/installer python3 -m app_installer uninstall
# Only when permanently deleting the single-host database is intended:
PYTHONPATH=deploy/installer python3 -m app_installer uninstall --remove-data
```

Both refuse a host with replication, promotion or backup state.

## Requirements

- Key-based SSH from the controller to every remote host, with the host key
  already in `known_hosts`. app-ops uses `BatchMode=yes` and
  `StrictHostKeyChecking=yes`, so it never prompts.
- Passwordless sudo (`NOPASSWD`) for the inventory user on every host,
  including the controller. app-ops runs privileged steps as `sudo -n` and
  never reads, sends or stores a password. A host that asks for one fails
  with a message saying so.
- Python 3 with PyYAML on the controller and the hosts. Nothing is rendered
  there, so no Jinja2: the hosts install the package's pre-rendered target
  files, filled in with their own address and the public hostnames.

On a controller where fapolicyd is active, app-ops is itself project Python.
Trust its files once before the first run, from the extracted operations
package:

```bash
sudo sh deploy/scripts/trust-files.sh trust todo \
  "$PWD"/deploy/dr/app_ops/*.py "$PWD"/deploy/installer/app_installer/*.py
```

After changing or replacing the package, run the same command again.

## Inventory

A small YAML file. The initial topology uses the roles `primary` and `standby`.
After promotion, the roles are `current_primary` and `rebuild_standby`.
Addresses must be literal IPv4 addresses. Mark the host you run on with
`local: true`.

```yaml
user: ops
# Optional; defaults to /home/<user> and <home>/todo-offline-m12.
home: /home/ops
bundle: /home/ops/todo-offline-m12
hosts:
  todo-primary: {role: primary, address: 192.0.2.10, local: true}
  todo-standby: {role: standby, address: 192.0.2.11}
```

## Commands

```bash
export PYTHONPATH="$PWD/deploy/dr" PYTHONDONTWRITEBYTECODE=1
python3 -m app_ops --inventory initial.yaml preflight-standby
python3 -m app_ops --inventory initial.yaml bootstrap-standby
python3 -m app_ops --inventory initial.yaml replication-status
python3 -m app_ops --inventory initial.yaml install-dr-tool
python3 -m app_ops --inventory initial.yaml install-quarantine-tool
python3 -m app_ops --inventory initial.yaml reseed-standby --confirm-reseed todo-standby
python3 -m app_ops --inventory initial.yaml nginx-tls-request --output ~/nginx-requests
python3 -m app_ops --inventory initial.yaml nginx-tls-install --certificates ~/nginx-signed --ca ~/ca.crt
python3 -m app_ops --inventory recovery.yaml failover \
  --confirm-primary-fenced "todo-primary is fenced" --confirm-promotion todo-standby
python3 -m app_ops --inventory recovery.yaml deploy-promoted-application
python3 -m app_ops --inventory recovery.yaml configure-backup
python3 -m app_ops --inventory recovery.yaml rebuild-standby \
  --confirm-fenced "todo-primary is fenced" --confirm-reseed todo-primary
python3 -m app_ops --inventory recovery.yaml cluster-status
```

`failover` is the one command for the surviving site after a person has
decided the primary is lost and fenced it. It runs on that host itself and
chains `app_dr.py promote`, `deploy-promoted-application`, `configure-backup`
and two checks: every service is ready and each app answers over HTTPS with
the host's CA, and each app's login can start (Keycloak shows its login form
for the app's client and redirect address, and the app's
Content-Security-Policy allows the token request). It does not log a user
in, which needs a person's password and a browser: confirm that by hand, or
with the browser test in acceptance. It stops at the first failed step and
names it; running it again skips a completed promotion and never retries a
failed one. Its result tells what users need: the hostnames, the address and
the CA fingerprint.

The public hostnames follow the primary. `standby` and `rebuild` read the
hostnames the current primary recorded (`app_dr_host target-values`) and give
them to the new standby, which records them too; `failover` deploys and checks
the promoted host with the names it recorded. Each host fills in its own
inventory address. See
[Primary and standby](../offline/README.md#primary-and-standby).

`nginx-tls-request` and `nginx-tls-install` give both hosts nginx certificates
from your own CA instead of the demo CA
([nginx certificates from your CA](#nginx-certificates-from-your-ca)).

`sync-standby-secrets` and `preflight-standby-rebuild` can also be run on
their own. Every command prints one JSON result: `changed`, or the status
report. `deploy-promoted-application` must run on the promoted host itself,
marked `local: true`.

## Scheduled check and nightly backup

Three user timers do the routine work, so nobody has to remember it. A failed
run leaves its service failed: that is the alert, seen with
`systemctl --user --failed` and in the journal (`journalctl --user -u NAME`).

| Timer | Installed by | Runs | What it does |
|---|---|---|---|
| `todo-dr-check.timer` | `install-dr-tool` on both hosts, `rebuild-standby` on the rebuilt one | every 15 minutes | `app_dr.py check`: each database's role, read live. A primary needs a standby streaming over TLS, slots that keep their WAL and, if archiving is on, a healthy archive; a standby must receive WAL. The group must not be split, and the disk under the home directory must be at least 10 % free. And the host must be ready to take over: its offline bundle is the revision of the operations package that ran `install-dr-tool`, every image archive the bundle lists is there, and it holds every DR secret, the replication CA included; it then prints `Ready to take over: ...`. |
| `todo-replication-tls.timer` | `install-dr-tool` on both hosts, `rebuild-standby` on the rebuilt one | every night at 03:30 (+ up to 30 min), and at boot if a night was missed | `app_dr.py renew-tls`: on the primary, a new replication certificate for every database with fewer than 30 days left, for the same address and from the same replication CA, then a PostgreSQL reload (no restart; the standby needs nothing new). On a standby it does nothing. The DR check above also fails once a certificate has fewer than 25 days left, or the replication CA fewer than 180 (the CA is replaced by hand). For a pair with [nginx certificates from your CA](#nginx-certificates-from-your-ca), the DR check fails below 30 days or when a host's certificate does not fit; renewing it is the two app-ops commands. |
| `todo-backup.timer` | `install.sh` on every server install; `configure-backup` (so `failover`) replaces its service on the current primary | every night at 02:30 (+ up to 30 min), and at boot if a night was missed | `app_backup.py nightly --keep-days 7`: a verified base backup of every database, then deletion of the backups older than 7 days (never the latest) and of the archived WAL older than the oldest kept backup (`pg_archivecleanup`). On a standby it does nothing. |

The units are in `deploy/dr/systemd` and go to `~/.config/systemd/user` on the
host. A host installed with `install.sh` already has `todo-backup.timer` from
the installer (`app_installer backup nightly`: base backups only, no WAL
archive); the timer has the same schedule and name, so `configure-backup`
only swaps its service for `app_backup.py nightly`, which adds the WAL
archive. A primary that was never promoted keeps the installer's nightly
backups, and a standby's skip: the primary takes the backups. The DR units run
the trusted tools in `/opt/todo/bin`. After a
failover the check fails on the promoted host until `rebuild-standby` gives it
a standby again, which is what it should report. WAL archiving, and so the
nightly backup with the WAL archive, starts with `configure-backup` after a
failover; until then a primary has the installer's nightly base backups
without PITR (backlog D2).

## nginx certificates from your CA

By default nginx makes its own demo CA when it first starts, so after a
failover the promoted host serves a new CA and every client must trust it
before users can work. In provided mode
([TLS.md](../../docs/TLS.md#provided-mode-a-separate-ca-process)) both
hosts hold a certificate from your CA (`app_ca.py`, which may run on either
host from its own storage, or your organisation's PKI) for the same public hostnames,
issued before it is needed, and clients trust that CA once. The standby,
which runs no nginx, keeps its key and certificate as Podman secrets on that
host (with the TLS volume: in the volume), which nginx gets after a failover.

```bash
# On the controller: a CSR from each host (each key stays in that host's Podman secrets).
python3 -m app_ops --inventory initial.yaml nginx-tls-request --output ~/nginx-requests
#   -> ~/nginx-requests/todo-primary.csr, ~/nginx-requests/todo-standby.csr
# With the CA (app_ca.py sign, or sudo todo-ca-sign in v1), for each host:
python3 deploy/scripts/app_ca.py sign --directory /media/ca-usb/todo-ca \
  --request todo-standby.csr --output todo-standby.crt
# Back on the controller, with both <host>.crt in one directory:
python3 -m app_ops --inventory initial.yaml nginx-tls-install --certificates ~/nginx-signed --ca ~/ca.crt
```

`nginx-tls-request` gives a standby the proxy image (from its offline
bundle) first; every openssl step runs in it. With the TLS volume it also
creates the volume from the bundle's own claim. The names come from each
host's record, the ones the primary serves. `nginx-tls-install` needs both
certificates before it changes anything, checks and installs the
standby's, then the primary's (nginx restarts, a few seconds; with the TLS
volume it reloads), and then sets the pair's mode to provided on both hosts
(`~/.config/todo/nginx-tls-mode`). From then on:

- `app_dr.py check` (every 15 minutes, both hosts) fails unless this host
  holds a certificate from your CA that fits its recorded hostnames, with
  at least 30 days left.
- Renewal is the same two commands again: `nginx-tls-request` makes each
  host a new key and request (the active certificate keeps serving), and
  `nginx-tls-install` installs the signed ones.
- `deploy-promoted-application`, and so `failover`, refuse to start nginx
  without that certificate: never a new demo CA by accident. `failover`
  then reports `"client_trust": "unchanged"`: nothing to do on the clients.

A host rebuilt as the standby keeps its certificate in its Podman secrets
(or TLS volume). A new machine needs its own: run the two commands again for
the pair. The nginx secrets are never in the DR secret copy: each host makes
its own key. In local mode `deploy-promoted-application` gives the promoted
host its own demo CA as Podman secrets before nginx starts, unless it has one.

## Operations package

Build one source-only package with app-ops, the installer module, the host
tools and the same pre-rendered target files and `bundle.json` as the offline
bundle:

```bash
deploy/scripts/build-operations-package.sh
```

Its `VERSION` file records the source Git revision and whether source changes
were present while it was built. Deploy only a reviewed `clean` artifact;
`dirty` is diagnostic provenance, not a release identifier.

## The workflows

Each page says what a command does, what it refuses and what evidence
acceptance records:

1. [Preparing the two hosts](STANDBY-ARCHITECTURE.md): accounts, SSH keys and
   the firewall rule.
2. [Standby bootstrap](STANDBY-BOOTSTRAP.md): `preflight-standby`,
   `bootstrap-standby`, `replication-status`.
3. [Promotion](PROMOTION.md): `install-dr-tool`, then the local `app_dr.py`.
4. [Application failover](APPLICATION-FAILOVER.md): `failover`, or
   `deploy-promoted-application` on its own.
5. [Backup and PITR](BACKUP-PITR.md): `configure-backup`, then the local
   `app_backup.py`.
6. [Restoring redundancy](RESTORE-REDUNDANCY.md): `preflight-standby-rebuild`,
   `rebuild-standby`, `cluster-status`, and `reseed-standby` for a standby
   that lost its slot.

For full validation, follow [ACCEPTANCE.md](../../docs/ACCEPTANCE.md).

## Hosts installed before the tool rename

The operator tools were renamed from `todo_dr.py`, `todo_backup.py` and
`todo-quarantine.sh` to `app_dr.py`, `app_backup.py` and `app-quarantine.sh`.
Host state keeps its names (`/opt/todo`, `~/.config/todo`, volumes and the
`todo` fapolicyd trust file). On a host that already has the old files:

1. Run `install-dr-tool`, `configure-backup` or `install-quarantine-tool`
   again so the new names are installed and trusted.
2. Point hypervisor-side quarantine calls at `/opt/todo/bin/app-quarantine.sh`.
   With `--enable-selinux-entrypoint`, remove the old file context with
   `sudo semanage fcontext -d '/opt/todo/bin/todo-quarantine\.sh'`.
3. Remove the old copies and their trust entries:

   ```bash
   cd /opt/todo/bin
   sudo fapolicyd-cli --file delete /opt/todo/bin/todo_dr.py --trust-file todo
   sudo fapolicyd-cli --file delete /opt/todo/bin/todo_backup.py --trust-file todo
   sudo fapolicyd-cli --file delete /opt/todo/bin/todo-quarantine.sh --trust-file todo
   sudo fapolicyd-cli --update
   sudo rm -f todo_dr.py todo_backup.py todo-quarantine.sh
   ```

`uninstall` still treats the old file names as clustered state and refuses.
