"""Replication of each database in the DR group: its role, and moving it between roles.

This module owns what a database is (primary or standby) and every step that
changes it. The functions come in this order:

- Read state: status, require_primary, require_standby, streaming_status,
  archive_health, cluster_status. They change nothing.
- Prepare a primary: configure_primary, publish_primaries, refresh_hba. The
  replication role, pg_hba access for the standby, and the ports 5432-5434 on
  the host's LAN address.
- Bootstrap a standby: authenticate, replication_path, bootstrap_standby. A
  pg_basebackup copy into an empty volume; existing data is refused.
- Promote: promote, require_promoted_group.
- Rebuild the old primary as a standby: rebuild_primary_check,
  require_quarantined_group, reseed_check, reseed_standby, reseed_group. Only
  an explicitly confirmed reseed deletes a data volume, and only after every
  check of the whole group passed.
- Copy a standby again without a failover (a lost slot): standby_slot,
  drop_idle_slot, standby_reseed_check, erase_standby_group. The standby must
  prove it is a read-only standby before anything on it is deleted.

Encryption of the stream is replication_tls.py: configure_primary and
publish_primaries call its ensure_ca and install_server_tls, and the standby
connects with sslmode=verify-full and the replication CA. Callers (app-ops)
own host fencing and the order of the group steps.
"""
import ipaddress
import json
import re
import secrets as random
import socket
import string
from pathlib import Path

from app_installer import apps, install, keycloak, quadlet, secrets, settings, workloads
from app_installer.commands import exists, run

from . import replication_tls

DATA = '/var/lib/postgresql/data'


def identifier(value):
    """Return value if it is a safe PostgreSQL role or slot name, else raise.

    Role and slot names are put into SQL text, so only lowercase letters,
    digits and underscores are allowed, as PostgreSQL identifiers.
    """
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', value):
        raise ValueError('Invalid PostgreSQL replication identifier')
    return value


def address(value):
    """Return value as a normalized IPv4 address; raise ValueError for anything else."""
    return str(ipaddress.IPv4Address(value))


def sql(database, statement, *, container=None, description=None,
        timeout: float = settings.COMMAND_TIMEOUT, secret_output=False, allowed=(0,)):
    """Run SQL with psql in the database's postgres database; the one way DR code runs SQL.

    The statement goes on stdin, never in argv. Fields come back separated by
    "|" with no header, and psql stops at the first error. container defaults
    to the database's own (a disposable restore passes its own), description
    names the step in an error, and secret_output hides psql's output in an
    error for SQL that holds a password (commands.run). allowed lets a caller
    that polls accept psql's failure codes; the output is then empty.
    """
    return run('podman', 'exec', '--interactive', container or database.container, 'psql',
               '--no-psqlrc', '--set', 'ON_ERROR_STOP=1', '--username', database.name,
               '--dbname', 'postgres', '--tuples-only', '--no-align', '--field-separator=|',
               input=statement + '\n', description=description or f'{database.name}: SQL',
               timeout=timeout, secret_output=secret_output, allowed=allowed).stdout.strip()


def lsn(value):
    """A WAL position such as '0/3000060' as a byte number: two hex halves, high/low 32 bits."""
    match = re.fullmatch(r'([0-9A-Fa-f]{1,8})/([0-9A-Fa-f]{1,8})', value)
    if not match:
        raise ValueError(f'Invalid WAL position: {value!r}')
    return int(match[1], 16) << 32 | int(match[2], 16)


def unreplayed_bytes(receive_lsn, replay_lsn):
    """WAL received but not replayed yet, in bytes; 0 when either position is unknown.

    After a walreceiver restart PostgreSQL reports the receive position as the
    start of the current WAL segment, so it can be *behind* the replay
    position until new WAL arrives. Nothing is left to replay then, so the
    result is 0, never negative.
    """
    if not receive_lsn or not replay_lsn:
        return 0
    return max(0, lsn(receive_lsn) - lsn(replay_lsn))


def status(database, query=None) -> dict:
    """Report whether the database is a standby, and how far it has replayed WAL.

    Returns in_recovery, transaction_read_only, the received and replayed LSNs
    and the bytes still to apply (unreplayed_bytes). query replaces sql() in tests.
    """
    query = query or sql
    fields = query(database, "SELECT pg_is_in_recovery(), current_setting('transaction_read_only'), "
                 "COALESCE(pg_last_wal_receive_lsn()::text, ''), "
                 "COALESCE(pg_last_wal_replay_lsn()::text, '');").split('|')
    if len(fields) != 4 or fields[0] not in ('t', 'f') or fields[1] not in ('on', 'off'):
        raise RuntimeError(f'{database.name}: invalid database status')
    try:
        lag = unreplayed_bytes(fields[2], fields[3])
    except ValueError as error:
        raise RuntimeError(f'{database.name}: invalid database status: {error}') from error
    return dict(in_recovery=fields[0] == 't', transaction_read_only=fields[1] == 'on',
                receive_lsn=fields[2], replay_lsn=fields[3], apply_lag_bytes=lag)


def require_primary(database, query=None) -> dict:
    """Return status() if the database is a writable primary, else raise."""
    state = status(database, query)
    if state['in_recovery'] or state['transaction_read_only']:
        raise RuntimeError(f'{database.name}: expected a writable primary')
    return state


def require_standby(database, query=None) -> dict:
    """Return status() if the database is a read-only standby with all received WAL replayed.

    Promotion calls this first: a standby that has not replayed everything it
    received would lose those transactions. A receive position behind the
    replay position is fine (see unreplayed_bytes); a missing one is not.
    """
    state = status(database, query)
    if not state['in_recovery'] or not state['transaction_read_only']:
        raise RuntimeError(f'{database.name}: expected a read-only standby')
    if not state['receive_lsn'] or not state['replay_lsn']:
        raise RuntimeError(f'{database.name}: standby receive or replay LSN is unavailable')
    if state['apply_lag_bytes'] != 0:
        raise RuntimeError(f"{database.name}: standby has unreplayed local WAL: {state['apply_lag_bytes']} bytes")
    return state


REFRESH_HBA_SCRIPT = """
set -eu
current=$(grep -E "^(host|hostssl) replication $1 " "$PGDATA/pg_hba.conf" || true)
if [ "$current" != "$2" ]; then
    sed -i -E "/^(host|hostssl) replication $1 /d" "$PGDATA/pg_hba.conf"
    printf '%s\\n' "$2" >> "$PGDATA/pg_hba.conf"
    printf 'changed\\n'
fi
"""


def refresh_hba(database):
    """Allow the replicator role to connect for replication from app-network only, over TLS.

    Writes one hostssl pg_hba.conf line for the current app-network subnet,
    replacing an older host or hostssl line for the same role, then reloads
    PostgreSQL. A connection without TLS then finds no matching line and is
    refused. Returns True if the line changed.
    """
    role = identifier(database.role('replicator'))
    network = json.loads(run('podman', 'network', 'inspect', apps.NETWORK).stdout)
    subnet = str(ipaddress.ip_network(network[0]['subnets'][0]['subnet']))
    rule = f'hostssl replication {role} {subnet} scram-sha-256'
    result = run('podman', 'exec', '-i', database.container, 'sh', '-s', '--',
                 role, rule, input=REFRESH_HBA_SCRIPT)
    sql(database, 'SELECT pg_reload_conf();')
    return result.stdout.strip() == 'changed'


def replication_probe(database, primary_address, *, local=False):
    """Log in as the replicator and run IDENTIFY_SYSTEM; return the psql result.

    With local=True the probe connects to this host's own container over
    app-network. Otherwise it connects to primary_address on the database's
    published replication port. Either way it requires TLS and checks the
    server's certificate against the replication CA (verify-full). The
    password comes from the Podman secret as an environment variable, never
    from argv. Exit code 2 (connection refused, TLS or login failed) is
    returned to the caller, not raised.
    """
    host = database.container if local else address(primary_address)
    port = 5432 if local else database.replication_port
    role = identifier(database.role('replicator'))
    ca = apps.REPLICATION_CA_SECRETS[1]
    return run('podman', 'run', '--rm', '--network', apps.NETWORK if local else 'host',
               '--user', 'postgres', '--security-opt', 'no-new-privileges', '--cap-drop', 'all',
               '--pids-limit', '64', '--secret', database.secret('replicator') + ',type=env,target=PGPASSWORD',
               '--secret', ca, database.image, 'psql',
               f'--dbname=host={host} port={port} user={role} replication=true connect_timeout=5 '
               f'sslmode=verify-full sslrootcert=/run/secrets/{ca}',
               '--no-psqlrc', '--no-password', '--tuples-only', '--no-align',
               '--command=IDENTIFY_SYSTEM;', allowed=(0, 2))


def authenticate(database, primary_address):
    """Prove the primary accepts this host's replication login; return its system ID.

    Bootstrap and reseed call this before they create or delete anything, so
    a wrong address or password stops the run while the old data still exists.
    """
    result = replication_probe(database, primary_address)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f'{database.name}: replication authentication failed; data was not removed')
    return result.stdout.strip().split('|')[0]


def replication_path(database, primary_address, *, timeout=5, connect=socket.create_connection):
    """Require a TCP connection from this host to the primary's replication port.

    The rebuild runs this after the primary has published the port on its
    LAN address and before anything is deleted, so an error names the cause
    (a firewall, or a port that is not published) instead of the later
    authentication check. It cannot run earlier: before publishing, a
    stateful firewall such as the Proxmox quarantine can drop the refusal,
    so an open path and a blocked one both time out.
    """
    target = (address(primary_address), database.replication_port)
    try:
        connect(target, timeout=timeout).close()
    except OSError as error:
        reason = 'timed out' if isinstance(error, TimeoutError) else error.strerror or str(error)
        raise RuntimeError(f'{database.name}: replication port {target[0]}:{target[1]} is not reachable ({reason}); '
                           'check the firewalls on both hosts; data was not removed') from None
    return 'open'


def configure_primary(database, node_address):
    """Make a writable primary ready to serve one standby. Safe to run again.

    Creates the replication secret if it is missing, turns on TLS with a
    certificate for node_address, allows replication logins over TLS in
    pg_hba.conf, and creates or repairs the replicator role (login
    and replication only, no other rights). It refuses an existing slot of
    the wrong type, but does not create the slot: pg_basebackup on the
    standby does. Returns True if anything changed.
    """
    address(node_address)
    require_primary(database)
    role = identifier(database.role('replicator'))
    slot = identifier(database.replication_slot())
    changed = False
    if not exists('secret', database.secret('replicator')):
        value = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(32))
        run('podman', 'secret', 'create', database.secret('replicator'), '-', input=value)
        changed = True
    value = secrets.read(database.secret('replicator'))
    if not re.fullmatch('[A-Za-z0-9]{32}', value):
        raise RuntimeError('The replication secret must be a 32-character alphanumeric value.')
    # TLS must be on before the hostssl line, or replication logins find no line.
    changed = replication_tls.install_server_tls(database, node_address) or changed
    changed = refresh_hba(database) or changed
    flags = sql(database, 'SELECT rolcanlogin, rolreplication, rolsuper, rolcreatedb, rolcreaterole, '
                f"rolinherit FROM pg_roles WHERE rolname = '{role}';")
    valid = flags == 't|t|f|f|f|f'
    if not valid or replication_probe(database, node_address, local=True).returncode:
        sql(database, f"DO $role$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f'THEN CREATE ROLE {role} LOGIN REPLICATION; END IF; END $role$; '
            f'ALTER ROLE {role} WITH LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE '
            f"NOINHERIT PASSWORD '{value}';", secret_output=True)
        changed = True
    # Bootstrap creates the slot, rather than reusing an unexplained existing one.
    existing = sql(database, 'SELECT slot_type, COALESCE(invalidation_reason, \'\') '
                   f"FROM pg_replication_slots WHERE slot_name = '{slot}';")
    if existing and existing != 'physical|':
        raise RuntimeError(f'{database.name}: replication slot is invalid or not physical')
    return changed


def publish_primaries(node_address, *, bootstrap, project_root, quadlet_dir, kube_runtime_dir, target):
    """LAN-publish every primary, restarting the app tier at most once; bootstrap also creates the replicator.

    Both forms create the replication CA if this host has none, and give
    every primary a TLS certificate for node_address before its hostssl line.
    target holds the bundle's files filled in for this host
    (target_render.load_on_host): each database gets its replicated unit,
    which publishes on node_address, and the readiness checks use the
    host's public hostnames.
    """
    node_address = address(node_address)
    install.preflight(quadlet_dir)
    for database in apps.REPLICATED_DATABASES:
        require_primary(database)
    access_changed = replication_tls.ensure_ca()
    restart = []
    for database in apps.REPLICATED_DATABASES:
        if bootstrap:
            changed = configure_primary(database, node_address)
        else:
            changed = replication_tls.install_server_tls(database, node_address)
            changed = refresh_hba(database) or changed
        access_changed = changed or access_changed
        if workloads.install_postgres(project_root, quadlet_dir, kube_runtime_dir, None,
                                      node_address, database=database, target=target):
            restart.append(database)
    if restart:
        run('systemctl', '--user', 'stop', *apps.services(databases=False), allowed=(0, 5))
    quadlet.systemctl('daemon-reload')
    for database in restart:
        quadlet.systemctl('restart', database.service)
    for database in apps.REPLICATED_DATABASES:
        run('podman', 'wait', '--condition=healthy', database.container,
            timeout=settings.HEALTH_TIMEOUT)
    quadlet.systemctl('start', 'shared-proxy.service')
    for hostname in target.hostnames.values():
        keycloak.wait('/ready', 30, 1, 'ready', hostname=hostname)
    return {'changed': access_changed or bool(restart), 'restarted': [database.name for database in restart]}


def data_claim(database, target):
    """Return the bundle's PersistentVolumeClaim for the database's data volume, as YAML.

    podman kube play creates the empty volume from this before
    pg_basebackup fills it, so the volume is exactly the one the workload
    YAML defines.
    """
    # YAML parsing is a DR-only dependency; never reconstruct the PVC in Python.
    import yaml
    claims = [document for document in yaml.safe_load_all(target.manifests[database.manifest].decode())
              if isinstance(document, dict) and document.get('kind') == 'PersistentVolumeClaim'
              and document.get('metadata', {}).get('name') == database.volume('data')]
    if len(claims) != 1:
        raise ValueError(f'{database.name}: expected exactly one canonical data PVC')
    return yaml.safe_dump(claims[0])


# $1 primary_address, $2 replication_port, $3 role, $4 slot, $5 passfile name,
# $6 replication secret name, $7 replication CA certificate secret name.
# pg_basebackup already wrote a bare primary_conninfo/primary_slot_name pair;
# this replaces both with values that include the passfile and require TLS
# checked against the CA, and writes the passfile and CA copy from the
# mounted secrets.
WRITE_RECOVERY_CONF_SCRIPT = """
set -eu
data=/var/lib/postgresql/data
passfile=$data/$5
ca=$data/replication-ca.crt
auto=$data/postgresql.auto.conf
umask 077
printf '%s:%s:replication:%s:' "$1" "$2" "$3" > "$passfile"
cat "/run/secrets/$6" >> "$passfile"
printf '\\n' >> "$passfile"
cat "/run/secrets/$7" > "$ca"
sed -i "/^primary_conninfo =/d; /^primary_slot_name =/d" "$auto"
printf "primary_conninfo = 'host=%s port=%s user=%s application_name=%s passfile=%s sslmode=verify-full sslrootcert=%s'\\n" \\
    "$1" "$2" "$3" "$4" "$passfile" "$ca" >> "$auto"
printf "primary_slot_name = '%s'\\n" "$4" >> "$auto"
"""


def bootstrap_standby(database, primary_address, *, project_root, quadlet_dir,
                      kube_runtime_dir, target, image_archive=None, slot=None):
    """Create a new read-only standby from a base backup of the primary.

    Refuses if the data volume already exists: bootstrap never overwrites
    data. Loads the PostgreSQL image from image_archive if it is missing,
    checks the replication login, then runs pg_basebackup over TLS checked
    against the replication CA, which also creates the replication slot on
    the primary. It then writes the recovery settings (again with TLS and
    verify-full), a passfile from the replication secret and a copy of the
    CA certificate, installs the database unit from the bundle's files
    (target, filled in for this host), starts it, and checks that it came up
    as a standby.
    """
    primary_address = address(primary_address)
    slot = identifier(slot or database.replication_slot())
    role = identifier(database.role('replicator'))
    claim = data_claim(database, target)
    if Path(kube_runtime_dir) != Path(quadlet_dir) / settings.KUBE_RUNTIME:
        raise ValueError(f'kube_runtime_dir must be quadlet_dir/{settings.KUBE_RUNTIME}')
    if exists('volume', database.volume('data')):
        raise RuntimeError(f'{database.volume("data")} already exists; bootstrap never overwrites data')
    if not exists('image', database.image):
        if image_archive is None or not Path(image_archive).is_file():
            raise RuntimeError('PostgreSQL image is missing; supply the verified offline archive')
        run('podman', 'load', '--input', image_archive)
    if not exists('secret', database.secret('replicator')):
        raise RuntimeError(f'{database.name}: replication secret is missing')
    ca = apps.REPLICATION_CA_SECRETS[1]
    if not exists('secret', ca):
        raise RuntimeError(f'{database.name}: replication CA certificate secret is missing')
    authenticate(database, primary_address)
    run('podman', 'kube', 'play', '-', input=claim)
    common = ('podman', 'run', '--rm', '--user', 'postgres', '--security-opt',
              'no-new-privileges', '--cap-drop', 'all')
    run(*common, '--network', 'host', '--pids-limit', '128', '--volume',
        f'{database.volume("data")}:{DATA}:U,Z', '--secret',
        database.secret('replicator') + ',type=env,target=PGPASSWORD', '--secret', ca,
        '--env', 'PGSSLMODE=verify-full', '--env', f'PGSSLROOTCERT=/run/secrets/{ca}', database.image,
        'pg_basebackup', f'--host={primary_address}', f'--port={database.replication_port}',
        f'--username={role}', f'--pgdata={DATA}', '--format=plain', '--wal-method=stream',
        '--write-recovery-conf', '--create-slot', f'--slot={slot}', '--progress',
        timeout=settings.DATA_COPY_TIMEOUT)
    run(*common, '--volume', f'{database.volume("data")}:{DATA}:Z', '--entrypoint', 'chmod',
        database.image, '0700', DATA)
    # The final helper must leave the shared Kube SELinux label, not a private MCS label.
    run(*common, '-i', '--volume', f'{database.volume("data")}:{DATA}:z', '--secret', database.secret('replicator'),
        '--secret', ca, '--entrypoint', '/bin/sh', database.image, '-s', '--',
        primary_address, str(database.replication_port), role, slot,
        database.replication_passfile(), database.secret('replicator'), ca, input=WRITE_RECOVERY_CONF_SCRIPT)
    workloads.install_postgres(project_root, quadlet_dir, kube_runtime_dir, None,
                               database=database, target=target)
    quadlet.systemctl('start', database.service)
    run('podman', 'wait', '--condition=healthy', database.container,
        timeout=settings.HEALTH_TIMEOUT)
    state = status(database)
    if not state['in_recovery'] or not state['transaction_read_only']:
        raise RuntimeError(f'{database.name}: bootstrap did not produce a read-only standby')
    return True


def promote(database, *, query=None, command=None):
    """Promote one standby and return its new status; only after fencing and the group preflight (app_dr.py promote)."""
    require_standby(database, query)
    command = command or run
    command('podman', 'exec', database.container, 'pg_ctl', '-D', DATA, 'promote', '-w', '-t', '60')
    return require_primary(database, query)


def streaming_status(database, *, rebuilt=False, slot=None):
    """On a primary: raise unless the standby on this slot streams asynchronously over TLS and the slot keeps its WAL.

    The slot is the bootstrap one, the rebuild one with rebuilt=True, or the
    one named by slot (a re-seeded standby keeps the slot it had).
    Returns the connection row and the slot row.
    """
    require_primary(database)
    slot = identifier(slot or database.replication_slot(rebuilt))
    fields = sql(database, "SELECT application_name, client_addr, state, sync_state, "
                 "pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint, COALESCE(ssl, false) "
                 "FROM pg_stat_replication LEFT JOIN pg_stat_ssl USING (pid) "
                 f"WHERE application_name = '{slot}';").split('|')
    if len(fields) != 6 or fields[2] != 'streaming' or fields[3] != 'async':
        raise RuntimeError(f'{database.name}: standby connection is not streaming asynchronously')
    if fields[5] != 't':
        raise RuntimeError(f'{database.name}: standby connection does not use TLS')
    health = sql(database, "SELECT slot_name, active, wal_status, safe_wal_size, "
                 "COALESCE(invalidation_reason, '') FROM pg_replication_slots "
                 f"WHERE slot_name = '{slot}';").split('|')
    if len(health) != 5 or health[1] != 't' or health[2] not in ('reserved', 'extended') or health[4]:
        raise RuntimeError(f'{database.name}: physical slot is inactive, losing WAL or invalidated')
    return {'connection': fields, 'slot': health}


def archive_health(database):
    """Writable primary whose WAL archiving has recovered from its most recent failure."""
    fields = sql(database, "SELECT pg_is_in_recovery(), current_setting('transaction_read_only'), "
                 "current_setting('archive_mode'), COALESCE(last_archived_wal, ''), failed_count, "
                 "CASE WHEN last_failed_time IS NULL OR (last_archived_time IS NOT NULL AND "
                 "last_archived_time >= last_failed_time) THEN 'healthy' ELSE 'failed' END "
                 "FROM pg_stat_archiver;").split('|')
    if len(fields) != 6 or fields[:3] != ['f', 'off', 'on'] or not fields[3] or fields[5] != 'healthy':
        raise RuntimeError(f'{database.name}: current primary is not writable, or WAL archiving has not '
                           'recovered from its most recent failure')
    return dict(zip(('in_recovery', 'read_only', 'archive_mode', 'last_archived_wal',
                     'historical_failures', 'archive_health'), fields))


def standby_streams(database):
    """On a primary: raise unless a standby streams over TLS and every physical slot is active and keeps its WAL.

    Unlike streaming_status it accepts any slot name, so it holds after
    bootstrap and after a rebuild alike. Returns how many standbys stream.
    """
    streaming = sql(database, "SELECT count(*) FROM pg_stat_replication JOIN pg_stat_ssl USING (pid) "
                    "WHERE state = 'streaming' AND ssl;")
    if streaming in ('', '0'):
        raise RuntimeError(f'{database.name}: no standby streams from this primary over TLS')
    slots = sql(database, "SELECT slot_name, active, wal_status, COALESCE(invalidation_reason, '') "
                "FROM pg_replication_slots WHERE slot_type = 'physical' ORDER BY slot_name;")
    for row in slots.splitlines():
        name, active, wal_status, reason = row.split('|')
        if active != 't' or wal_status not in ('reserved', 'extended') or reason:
            raise RuntimeError(f'{database.name}: replication slot {name} is inactive, losing WAL or invalidated')
    return int(streaming)


def receiving(database):
    """On a standby: raise unless its WAL receiver streams from the primary."""
    state = sql(database, "SELECT COALESCE((SELECT status FROM pg_stat_wal_receiver), '');")
    if state != 'streaming':
        raise RuntimeError(f'{database.name}: the standby does not receive WAL from the primary '
                           f'({state or "no WAL receiver"})')


def archiving(database):
    """On a primary: False if WAL archiving is off; True if it is on and healthy; else raise."""
    if sql(database, "SELECT current_setting('archive_mode');") != 'on':
        return False
    archive_health(database)
    return True


def cluster_status(role):
    """Read-only group report after rebuild; every database is checked before any failure is raised."""
    report, problems = {}, []
    for database in apps.REPLICATED_DATABASES:
        try:
            if role == 'primary':
                report[database.name] = {'replication': streaming_status(database, rebuilt=True),
                                    'archive': archive_health(database)}
            else:
                state = status(database)
                if not (state['in_recovery'] and state['transaction_read_only']
                        and state['receive_lsn'] and state['replay_lsn']):
                    raise RuntimeError(f'{database.name}: rebuilt standby is not a read-only recovering database')
                report[database.name] = state
        except RuntimeError as error:
            problems.append(str(error))
    if problems:
        raise RuntimeError('; '.join(problems))
    return report


def require_promoted_group(journal_path):
    """Raise unless the promotion record confirms the whole group and every database is an active, healthy primary.

    The steps that open a promoted host to users (deploy-promoted,
    configure-backup) call this first, so a failed or partial promotion is
    never exposed.
    """
    names = [database.name for database in apps.REPLICATED_DATABASES]
    try:
        decision = json.loads(Path(journal_path).read_text())
    except (OSError, ValueError) as error:
        raise RuntimeError('A readable completed group promotion record is required') from error
    if (decision.get('state') != 'complete' or decision.get('applications') != names
            or decision.get('completed') != names):
        raise RuntimeError('Promotion record does not confirm the complete database group')
    for database in apps.REPLICATED_DATABASES:
        if run('systemctl', '--user', 'is-active', database.service).stdout.strip() != 'active':
            raise RuntimeError(f'{database.name}: database service is not active')
        if run('podman', 'inspect', '--format', '{{.State.Health.Status}}',
               database.container).stdout.strip() != 'healthy':
            raise RuntimeError(f'{database.name}: database is not healthy')
        require_primary(database)
    return False


def require_stopped_service(service):
    """Raise unless the user service is loaded, stopped and has no processes left."""
    output = run('systemctl', '--user', 'show', service, '--property=LoadState',
                 '--property=ActiveState', '--property=MainPID', '--property=ControlPID').stdout
    fields = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    if (fields.get('LoadState') != 'loaded' or fields.get('ActiveState') not in ('inactive', 'failed')
            or fields.get('MainPID') != '0' or fields.get('ControlPID') != '0'):
        raise RuntimeError(f'{service}: require loaded, stopped service with zero MainPID/ControlPID')


def require_quarantined_group():
    """Raise unless every service is stopped and no user container runs.

    This is what the quarantine stop helper leaves behind. The rebuild host
    must look like this before anything on it is deleted.
    """
    for service in apps.services():
        require_stopped_service(service)
    if run('podman', 'ps', '--format', '{{.Names}}').stdout.strip():
        raise RuntimeError('Running user containers remain; keep infrastructure quarantine in place')
    return False


def rebuild_primary_check(database):
    """Read-only check that the current primary can accept a rebuilt standby.

    The database must be a writable, active primary with its replication
    secret, backup volume and replication role. The rebuild slot must not
    exist yet: if it does, an earlier rebuild stopped partway, and that
    needs a person to look at it before anything is retried.
    """
    require_primary(database)
    if run('systemctl', '--user', 'is-active', database.service).stdout.strip() != 'active':
        raise RuntimeError(f'{database.name}: current database service is not active')
    for kind, name in (('secret', database.secret('replicator')), ('volume', database.volume('backup'))):
        if not exists(kind, name):
            raise RuntimeError(f'{database.name}: required {kind} {name} is missing')
    role = identifier(database.role('replicator'))
    if sql(database, f"SELECT rolreplication FROM pg_roles WHERE rolname = '{role}';") != 't':
        raise RuntimeError(f'{database.name}: replication role is missing or invalid')
    slot = identifier(database.replication_slot(rebuilt=True))
    if sql(database, f"SELECT count(*) FROM pg_replication_slots WHERE slot_name = '{slot}';") != '0':
        raise RuntimeError(f'{database.name}: rebuild slot already exists; never retry a partial rebuild blindly')
    return False


def require_reseed_confirmations(confirm_fenced, confirm_reseed):
    """Raise unless both confirmations name this host exactly.

    confirm_fenced must be "<hostname> is fenced" and confirm_reseed must be
    "<hostname>". Typing the name of the host being erased is the operator's
    explicit consent to delete its data.
    """
    host = socket.gethostname()
    if confirm_fenced != host + ' is fenced' or confirm_reseed != host:
        raise RuntimeError('Exact local hostname and infrastructure-fencing confirmations are required')


def reseed_check(database, primary_address, *, project_root, quadlet_dir, kube_runtime_dir,
                 target, confirm_fenced, confirm_reseed):
    """Local-only checks; never contacts the primary.

    A rebuild's read-only preflight runs this before the primary has
    published its LAN replication endpoint (that happens later, in the
    same rebuild run). Only ``reseed_standby`` authenticates, immediately
    before it deletes the old volume.
    """
    require_reseed_confirmations(confirm_fenced, confirm_reseed)
    address(primary_address)
    directory, runtime = Path(quadlet_dir), Path(kube_runtime_dir)
    if runtime != directory / settings.KUBE_RUNTIME or runtime.is_symlink():
        raise ValueError(f'kube_runtime_dir must be quadlet_dir/{settings.KUBE_RUNTIME}')
    if run('podman', 'info', '--format', '{{.Host.Security.Rootless}}').stdout.strip() != 'true':
        raise RuntimeError('Destructive reseed requires rootless Podman')
    require_stopped_service(database.service)
    if run('podman', 'ps', '--filter', 'name=^' + database.container + '$',
           '--format', '{{.Names}}').stdout.strip():
        raise RuntimeError(f'{database.name}: PostgreSQL is still running')
    for kind, name in (('volume', database.volume('data')), ('image', database.image),
                       ('secret', database.secret('replicator')), ('secret', database.secret('db'))):
        if not exists(kind, name):
            raise RuntimeError(f'{database.name}: required {kind} {name} is missing; data was not removed')
    if (directory / (database.container + '.container')).exists():
        raise RuntimeError('Destructive reseed refuses a legacy PostgreSQL container Quadlet')
    if '--no-pod-prefix' not in run('podman', 'kube', 'play', '--help').stdout:
        raise RuntimeError('Destructive reseed requires Podman --no-pod-prefix')
    # Every file the reseed installs must be in the bundle before anything is erased,
    # the canonical PVC included; target_render.load already filled them all in.
    data_claim(database, target)
    missing = [name for name, files in ((database.config_manifest, target.manifests),
                                        (database.unit, target.quadlets)) if name not in files]
    if missing:
        raise RuntimeError(f'{database.name}: the bundle lacks {", ".join(missing)}; data was not removed')
    return False


def remove_exited_containers_using(volume):
    """Clear only containers a hard host fence left stopped on this volume.

    Podman refuses to remove a volume that any container still references,
    even one that already exited; a bare hypervisor power-off (as fencing
    requires) never runs `podman kube down`, so the old workload's exited
    containers are exactly what is left. Never removes a running container;
    that must fail closed instead, same as an in-use volume.
    """
    entries = [line.split('|', 1) for line in
              run('podman', 'ps', '-a', '--filter', f'volume={volume}',
                  '--format', '{{.Names}}|{{.State}}').stdout.splitlines() if line]
    running = [name for name, state in entries if state == 'running']
    if running:
        raise RuntimeError(f'{volume}: container {running[0]} is still running; data was not removed')
    names = [name for name, _ in entries]
    if names:
        run('podman', 'rm', *names)
    return bool(names)


def reseed_standby(database, primary_address, *, confirm_fenced, confirm_reseed, **paths):
    """Delete this database's data volume and bootstrap it again as a standby of the primary.

    Only reseed_group calls it, after the checks of the whole group passed;
    it repeats this database's checks and the replication login first.
    """
    reseed_check(database, primary_address, confirm_fenced=confirm_fenced,
                 confirm_reseed=confirm_reseed, **paths)
    # Authenticate against the primary's now-published LAN endpoint as the
    # last check before the destructive step; reseed_check cannot do this
    # (see its docstring).
    authenticate(database, primary_address)
    remove_exited_containers_using(database.volume('data'))
    # No force and no backup-volume removal. In-use data must fail closed.
    run('podman', 'volume', 'rm', database.volume('data'))
    return bootstrap_standby(database, primary_address, slot=database.replication_slot(rebuilt=True), **paths)


# Application-tier runtime files the rebuilt standby must not start again.
SHARED_TIER_FILES = ('keycloak.kube', 'shared-proxy.kube', 'keycloak.yaml', 'shared-proxy.yaml')


def reseed_group(primary_address, *, confirm_fenced, confirm_reseed, **paths):
    """Rebuild every database as a standby; every local and primary check passes before the first deletion."""
    require_reseed_confirmations(confirm_fenced, confirm_reseed)
    primary_address = address(primary_address)
    run('systemctl', '--user', 'stop', *apps.services(), allowed=(0, 5))
    require_quarantined_group()
    confirmations = dict(confirm_fenced=confirm_fenced, confirm_reseed=confirm_reseed)
    for database in apps.REPLICATED_DATABASES:
        reseed_check(database, primary_address, **confirmations, **paths)
    for database in apps.REPLICATED_DATABASES:
        authenticate(database, primary_address)
    runtime = Path(paths['kube_runtime_dir'])
    for name in SHARED_TIER_FILES + tuple(name for app in apps.APPS
                                          for name in (app.unit, app.manifest)):
        (runtime / name).unlink(missing_ok=True)
    for database in apps.REPLICATED_DATABASES:
        reseed_standby(database, primary_address, **confirmations, **paths)
    return [database.name for database in apps.REPLICATED_DATABASES]


def standby_slot(database):
    """On a primary: the name of the one physical slot its standby uses, or the bootstrap name if there is none.

    A pair has one standby, so more than one physical slot is something a
    person must look at; this refuses instead of guessing which to drop.
    A re-seed gives the standby the same slot name again, so the checks that
    name the slot (cluster-status after a rebuild) still hold afterwards.
    """
    require_primary(database)
    names = sql(database, "SELECT slot_name FROM pg_replication_slots WHERE slot_type = 'physical' "
                "ORDER BY slot_name;").split()
    if len(names) > 1:
        raise RuntimeError(f'{database.name}: more than one replication slot ({", ".join(names)}); '
                           'a pair has one standby, so nothing was changed')
    return identifier(names[0] if names else database.replication_slot())


def drop_idle_slot(database, slot):
    """On a primary: drop slot if it exists and no standby is connected to it; True if it was dropped.

    The re-seed stops the standby's databases first; their WAL senders end a
    moment later, so app-ops retries while this raises that the slot is still
    in use. An active slot is never dropped.
    """
    require_primary(database)
    slot = identifier(slot)
    active = sql(database, f"SELECT active FROM pg_replication_slots WHERE slot_name = '{slot}';")
    if not active:
        return False
    if active != 'f':
        raise RuntimeError(f'{database.name}: replication slot {slot} is still in use; the standby has not stopped')
    sql(database, f"SELECT pg_drop_replication_slot('{slot}');")
    return True


def standby_reseed_check(primary_address):
    """On a standby, read-only: it may be copied again from the primary at primary_address.

    Every database must run as a read-only standby: that is the proof this
    host is not the primary, so a mistaken inventory erases nothing. No
    application service may be active (a database-only standby), and every
    database must reach the primary's replication port and log in there
    over TLS, so a wrong address or password stops the run while the old
    copy still exists. A standby that is down must be started first; it
    comes up in recovery and waits for the primary.
    """
    primary_address = address(primary_address)
    for database in apps.REPLICATED_DATABASES:
        state = status(database)
        if not (state['in_recovery'] and state['transaction_read_only']):
            raise RuntimeError(f'{database.name}: this host is not a read-only standby; '
                               'a re-seed never erases a primary, so nothing was changed')
    services = apps.services(databases=False)
    states = run('systemctl', '--user', 'is-active', *services, allowed=(0, 3, 4)).stdout.split()
    running = [service for service, state in zip(services, states) if state not in ('inactive', 'failed')]
    if running:
        raise RuntimeError(f'the application tier runs here ({", ".join(running)}); '
                           'only a database-only standby is re-seeded, so nothing was changed')
    for database in apps.REPLICATED_DATABASES:
        replication_path(database, primary_address)
        authenticate(database, primary_address)
    return False


def erase_standby_group(primary_address, confirm_reseed):
    """On a standby: stop its databases and delete their data volumes, after every check passed again.

    confirm_reseed must be this host's name. The backup volumes and the
    secrets stay; bootstrap_standby then copies each database again. If the
    run stops after this, the primary is untouched, and bootstrap-standby
    builds the standby from here (its volumes are gone, as it requires).
    """
    if confirm_reseed != socket.gethostname():
        raise RuntimeError('The exact local hostname is required to erase this standby')
    standby_reseed_check(primary_address)
    run('systemctl', '--user', 'stop', *(database.service for database in apps.REPLICATED_DATABASES))
    for database in apps.REPLICATED_DATABASES:
        require_stopped_service(database.service)
        remove_exited_containers_using(database.volume('data'))
        # No force and no backup-volume removal. In-use data must fail closed.
        run('podman', 'volume', 'rm', database.volume('data'))
    return [database.name for database in apps.REPLICATED_DATABASES]
