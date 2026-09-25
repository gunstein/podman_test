"""Per-database replication mechanics; callers own host fencing and group gates.

Initial bootstrap refuses existing data. The physical slot is created by
pg_basebackup, as in the original Ansible role, so a partial attempt is never
silently treated as a successful bootstrap. Only explicitly confirmed reseed deletes the selected old data volume, after group gates.
"""
import ipaddress
import json
import re
import secrets as random
import socket
import string
from pathlib import Path

from . import apps, quadlet, secrets, workloads
from .commands import exists, run

DATA = '/var/lib/postgresql/data'


def identifier(value):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', value):
        raise ValueError('Invalid PostgreSQL replication identifier')
    return value


def address(value):
    return str(ipaddress.IPv4Address(value))


def sql(app, statement, *, database='postgres'):
    return run('podman', 'exec', '--interactive', app.resource('postgres'), 'psql',
               '--no-psqlrc', '--set', 'ON_ERROR_STOP=1', '--username', app.name,
               '--dbname', database, '--tuples-only', '--no-align', '--field-separator=|',
               input=statement + '\n').stdout.strip()


def status(app, query=None):
    query = query or sql
    fields = query(app, "SELECT pg_is_in_recovery(), current_setting('transaction_read_only'), "
                 "COALESCE(pg_last_wal_receive_lsn()::text, ''), "
                 "COALESCE(pg_last_wal_replay_lsn()::text, ''), "
                 "COALESCE(pg_wal_lsn_diff(pg_last_wal_receive_lsn(), "
                 "pg_last_wal_replay_lsn())::bigint, 0);").split('|')
    if len(fields) != 5 or fields[0] not in ('t', 'f') or fields[1] not in ('on', 'off'):
        raise RuntimeError(f'{app.name}: invalid database status')
    return dict(in_recovery=fields[0] == 't', transaction_read_only=fields[1] == 'on',
                receive_lsn=fields[2], replay_lsn=fields[3], apply_lag_bytes=int(fields[4]))


def require_primary(app, query=None):
    state = status(app, query)
    if state['in_recovery'] or state['transaction_read_only']:
        raise RuntimeError(f'{app.name}: expected a writable primary')
    return state


def require_standby(app, query=None):
    state = status(app, query)
    if not state['in_recovery'] or not state['transaction_read_only']:
        raise RuntimeError(f'{app.name}: expected a read-only standby')
    if not state['receive_lsn'] or not state['replay_lsn'] or state['apply_lag_bytes'] != 0:
        raise RuntimeError(f'{app.name}: standby WAL is unavailable or not fully replayed')
    return state


REFRESH_HBA_SCRIPT = """
set -eu
current=$(grep "^host replication $1 " "$PGDATA/pg_hba.conf" || true)
if [ "$current" != "$2" ]; then
    sed -i "/^host replication $1 /d" "$PGDATA/pg_hba.conf"
    printf '%s\\n' "$2" >> "$PGDATA/pg_hba.conf"
    printf 'changed\\n'
fi
"""


def refresh_hba(app):
    role = identifier(app.database_role('replicator'))
    network = json.loads(run('podman', 'network', 'inspect', apps.NETWORK).stdout)
    subnet = str(ipaddress.ip_network(network[0]['subnets'][0]['subnet']))
    rule = f'host replication {role} {subnet} scram-sha-256'
    result = run('podman', 'exec', '-i', app.resource('postgres'), 'sh', '-s', '--',
                 role, rule, input=REFRESH_HBA_SCRIPT)
    sql(app, 'SELECT pg_reload_conf();')
    return result.stdout.strip() == 'changed'


def replication_probe(app, primary_address, *, local=False):
    host = app.resource('postgres') if local else address(primary_address)
    port = 5432 if local else app.replication_port
    role = identifier(app.database_role('replicator'))
    return run('podman', 'run', '--rm', '--network', apps.NETWORK if local else 'host',
               '--user', 'postgres', '--security-opt', 'no-new-privileges', '--cap-drop', 'all',
               '--pids-limit', '64', '--secret', app.secret('replicator') + ',type=env,target=PGPASSWORD',
               app.image('postgres'), 'psql',
               f'--dbname=host={host} port={port} user={role} replication=true connect_timeout=5',
               '--no-psqlrc', '--no-password', '--tuples-only', '--no-align',
               '--command=IDENTIFY_SYSTEM;', allowed=(0, 2))


def authenticate(app, primary_address):
    result = replication_probe(app, primary_address)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f'{app.name}: replication authentication failed; data was not removed')
    return result.stdout.strip().split('|')[0]


def configure_primary(app, node_address):
    address(node_address)
    require_primary(app)
    role = identifier(app.database_role('replicator'))
    slot = identifier(app.replication_slot())
    changed = False
    if not exists('secret', app.secret('replicator')):
        value = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(32))
        run('podman', 'secret', 'create', app.secret('replicator'), '-', input=value)
        changed = True
    value = secrets.read(app.secret('replicator'))
    if not re.fullmatch('[A-Za-z0-9]{32}', value):
        raise RuntimeError('The replication secret must be a 32-character alphanumeric value.')
    changed = refresh_hba(app) or changed
    flags = sql(app, 'SELECT rolcanlogin, rolreplication, rolsuper, rolcreatedb, rolcreaterole, '
                f"rolinherit FROM pg_roles WHERE rolname = '{role}';")
    valid = flags == 't|t|f|f|f|f'
    if not valid or replication_probe(app, node_address, local=True).returncode:
        sql(app, f"DO $role$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f'THEN CREATE ROLE {role} LOGIN REPLICATION; END IF; END $role$; '
            f'ALTER ROLE {role} WITH LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE '
            f"NOINHERIT PASSWORD '{value}';")
        changed = True
    # Bootstrap creates the slot, rather than reusing an unexplained existing one.
    existing = sql(app, 'SELECT slot_type, COALESCE(invalidation_reason, \'\') '
                   f"FROM pg_replication_slots WHERE slot_name = '{slot}';")
    if existing and existing != 'physical|':
        raise RuntimeError(f'{app.name}: replication slot is invalid or not physical')
    return changed


def data_claim(app, rendered_manifest_dir):
    # YAML parsing is a DR-only dependency; never reconstruct the PVC in Python.
    import yaml
    path = Path(rendered_manifest_dir) / app.manifest('postgres')
    claims = [document for document in yaml.safe_load_all(path.read_text())
              if isinstance(document, dict) and document.get('kind') == 'PersistentVolumeClaim'
              and document.get('metadata', {}).get('name') == app.volume('data')]
    if len(claims) != 1:
        raise ValueError(f'{app.name}: expected exactly one canonical data PVC')
    return yaml.safe_dump(claims[0])


# $1 primary_address, $2 replication_port, $3 role, $4 slot, $5 passfile name,
# $6 replication secret name. pg_basebackup already wrote a bare
# primary_conninfo/primary_slot_name pair; this replaces both with values that
# include the passfile, and writes that passfile from the mounted secret.
WRITE_RECOVERY_CONF_SCRIPT = """
set -eu
data=/var/lib/postgresql/data
passfile=$data/$5
auto=$data/postgresql.auto.conf
umask 077
printf '%s:%s:replication:%s:' "$1" "$2" "$3" > "$passfile"
cat "/run/secrets/$6" >> "$passfile"
printf '\\n' >> "$passfile"
sed -i "/^primary_conninfo =/d; /^primary_slot_name =/d" "$auto"
printf "primary_conninfo = 'host=%s port=%s user=%s application_name=%s passfile=%s'\\n" \\
    "$1" "$2" "$3" "$4" "$passfile" >> "$auto"
printf "primary_slot_name = '%s'\\n" "$4" >> "$auto"
"""


def bootstrap_standby(app, primary_address, *, project_root, quadlet_dir,
                      kube_runtime_dir, rendered_manifest_dir, image_archive=None, slot=None):
    primary_address = address(primary_address)
    slot = identifier(slot or app.replication_slot())
    role = identifier(app.database_role('replicator'))
    claim = data_claim(app, rendered_manifest_dir)
    if Path(kube_runtime_dir) != Path(quadlet_dir) / 'todo-kube-runtime':
        raise ValueError('kube_runtime_dir must be quadlet_dir/todo-kube-runtime')
    if exists('volume', app.volume('data')):
        raise RuntimeError(f'{app.volume("data")} already exists; bootstrap never overwrites data')
    if not exists('image', app.image('postgres')):
        if image_archive is None or not Path(image_archive).is_file():
            raise RuntimeError('PostgreSQL image is missing; supply the verified offline archive')
        run('podman', 'load', '--input', image_archive)
    if not exists('secret', app.secret('replicator')):
        raise RuntimeError(f'{app.name}: replication secret is missing')
    authenticate(app, primary_address)
    run('podman', 'kube', 'play', '-', input=claim)
    common = ('podman', 'run', '--rm', '--user', 'postgres', '--security-opt',
              'no-new-privileges', '--cap-drop', 'all')
    run(*common, '--network', 'host', '--pids-limit', '128', '--volume',
        f'{app.volume("data")}:{DATA}:U,Z', '--secret',
        app.secret('replicator') + ',type=env,target=PGPASSWORD', app.image('postgres'),
        'pg_basebackup', f'--host={primary_address}', f'--port={app.replication_port}',
        f'--username={role}', f'--pgdata={DATA}', '--format=plain', '--wal-method=stream',
        '--write-recovery-conf', '--create-slot', f'--slot={slot}', '--progress')
    run(*common, '--volume', f'{app.volume("data")}:{DATA}:Z', '--entrypoint', 'chmod',
        app.image('postgres'), '0700', DATA)
    # The final helper must leave the shared Kube SELinux label, not a private MCS label.
    run(*common, '-i', '--volume', f'{app.volume("data")}:{DATA}:z', '--secret', app.secret('replicator'),
        '--entrypoint', '/bin/sh', app.image('postgres'), '-s', '--',
        primary_address, str(app.replication_port), role, slot,
        app.replication_passfile(), app.secret('replicator'), input=WRITE_RECOVERY_CONF_SCRIPT)
    workloads.install_postgres(project_root, quadlet_dir, kube_runtime_dir,
                               rendered_manifest_dir, app=app)
    quadlet.systemctl('start', app.service('postgres'))
    run('podman', 'wait', '--condition=healthy', app.resource('postgres'))
    state = status(app)
    if not state['in_recovery'] or not state['transaction_read_only']:
        raise RuntimeError(f'{app.name}: bootstrap did not produce a read-only standby')
    return True


def promote(app, *, query=None, command=None):
    """Low-level promotion; only call after fencing and all-app preflight gates."""
    require_standby(app, query)
    command = command or run
    command('podman', 'exec', app.resource('postgres'), 'pg_ctl', '-D', DATA, 'promote', '-w', '-t', '60')
    return require_primary(app, query)


def streaming_status(app, *, rebuilt=False):
    """Verify an independent sender and its usable physical slot."""
    require_primary(app)
    slot = identifier(app.replication_slot(rebuilt))
    fields = sql(app, "SELECT application_name, client_addr, state, sync_state, "
                 "pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint "
                 f"FROM pg_stat_replication WHERE application_name = '{slot}';").split('|')
    if len(fields) != 5 or fields[2] != 'streaming':
        raise RuntimeError(f'{app.name}: standby connection is not streaming')
    health = sql(app, "SELECT slot_name, active, wal_status, safe_wal_size, "
                 "COALESCE(invalidation_reason, '') FROM pg_replication_slots "
                 f"WHERE slot_name = '{slot}';").split('|')
    if len(health) != 5 or health[1] != 't' or health[2] not in ('reserved', 'extended') or health[4]:
        raise RuntimeError(f'{app.name}: physical slot is inactive, losing WAL or invalidated')
    return {'connection': fields, 'slot': health}


def require_promoted_group(journal_path):
    """Never expose an incomplete group, including a failed final verification."""
    names = [app.name for app in apps.REPLICATED_DATABASES]
    try:
        decision = json.loads(Path(journal_path).read_text())
    except (OSError, ValueError) as error:
        raise RuntimeError('A readable completed group promotion record is required') from error
    if (decision.get('state') != 'complete' or decision.get('applications') != names
            or decision.get('completed') != names):
        raise RuntimeError('Promotion record does not confirm the complete database group')
    for app in apps.REPLICATED_DATABASES:
        if run('systemctl', '--user', 'is-active', app.service('postgres')).stdout.strip() != 'active':
            raise RuntimeError(f'{app.name}: database service is not active')
        if run('podman', 'inspect', '--format', '{{.State.Health.Status}}',
               app.resource('postgres')).stdout.strip() != 'healthy':
            raise RuntimeError(f'{app.name}: database is not healthy')
        require_primary(app)
    return False


def require_stopped_service(service):
    output = run('systemctl', '--user', 'show', service, '--property=LoadState',
                 '--property=ActiveState', '--property=MainPID', '--property=ControlPID').stdout
    fields = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    if (fields.get('LoadState') != 'loaded' or fields.get('ActiveState') not in ('inactive', 'failed')
            or fields.get('MainPID') != '0' or fields.get('ControlPID') != '0'):
        raise RuntimeError(f'{service}: require loaded, stopped service with zero MainPID/ControlPID')


def require_quarantined_group():
    for service in apps.services():
        require_stopped_service(service)
    if run('podman', 'ps', '--format', '{{.Names}}').stdout.strip():
        raise RuntimeError('Running user containers remain; keep infrastructure quarantine in place')
    return False


def rebuild_primary_check(app):
    require_primary(app)
    if run('systemctl', '--user', 'is-active', app.service('postgres')).stdout.strip() != 'active':
        raise RuntimeError(f'{app.name}: current database service is not active')
    for kind, name in (('secret', app.secret('replicator')), ('volume', app.volume('backup'))):
        if not exists(kind, name):
            raise RuntimeError(f'{app.name}: required {kind} {name} is missing')
    role = identifier(app.database_role('replicator'))
    if sql(app, f"SELECT rolreplication FROM pg_roles WHERE rolname = '{role}';") != 't':
        raise RuntimeError(f'{app.name}: replication role is missing or invalid')
    slot = identifier(app.replication_slot(rebuilt=True))
    if sql(app, f"SELECT count(*) FROM pg_replication_slots WHERE slot_name = '{slot}';") != '0':
        raise RuntimeError(f'{app.name}: rebuild slot already exists; never retry a partial rebuild blindly')
    return False


def reseed_check(app, primary_address, *, project_root, quadlet_dir, kube_runtime_dir,
                 rendered_manifest_dir, confirm_fenced, confirm_reseed):
    """Local-only checks; never contacts the primary.

    A rebuild's read-only preflight runs this before the primary has
    published its LAN replication endpoint (that happens later, in the
    same rebuild run). Only ``reseed_standby`` authenticates, immediately
    before it deletes the old volume.
    """
    host = socket.gethostname()
    if confirm_fenced != host + ' is fenced' or confirm_reseed != host:
        raise RuntimeError('Exact local hostname and infrastructure-fencing confirmations are required')
    address(primary_address)
    directory, runtime = Path(quadlet_dir), Path(kube_runtime_dir)
    if runtime != directory / 'todo-kube-runtime' or runtime.is_symlink():
        raise ValueError('kube_runtime_dir must be quadlet_dir/todo-kube-runtime')
    if run('podman', 'info', '--format', '{{.Host.Security.Rootless}}').stdout.strip() != 'true':
        raise RuntimeError('Destructive reseed requires rootless Podman')
    require_stopped_service(app.service('postgres'))
    if run('podman', 'ps', '--filter', 'name=^' + app.resource('postgres') + '$',
           '--format', '{{.Names}}').stdout.strip():
        raise RuntimeError(f'{app.name}: PostgreSQL is still running')
    for kind, name in (('volume', app.volume('data')), ('image', app.image('postgres')),
                       ('secret', app.secret('replicator')), ('secret', app.secret('db'))):
        if not exists(kind, name):
            raise RuntimeError(f'{app.name}: required {kind} {name} is missing; data was not removed')
    if (directory / (app.resource('postgres') + '.container')).exists():
        raise RuntimeError('Destructive reseed refuses a legacy PostgreSQL container Quadlet')
    if '--no-pod-prefix' not in run('podman', 'kube', 'play', '--help').stdout:
        raise RuntimeError('Destructive reseed requires Podman --no-pod-prefix')
    # Validate every file before erasing anything, including the canonical PVC.
    data_claim(app, rendered_manifest_dir)
    (Path(rendered_manifest_dir) / app.manifest('config')).read_bytes()
    (Path(project_root) / 'deploy/quadlet/app-network.network').read_bytes()
    quadlet.render(project_root, app.unit('postgres'), {
        'postgres_publish_address': '', 'postgres_publish_port': app.replication_port})
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


def reseed_standby(app, primary_address, *, confirm_fenced, confirm_reseed, **paths):
    """One explicitly confirmed replacement, after the caller's all-app gates."""
    reseed_check(app, primary_address, confirm_fenced=confirm_fenced,
                 confirm_reseed=confirm_reseed, **paths)
    # Authenticate against the primary's now-published LAN endpoint as the
    # last check before the destructive step; reseed_check cannot do this
    # (see its docstring).
    authenticate(app, primary_address)
    remove_exited_containers_using(app.volume('data'))
    # No force and no backup-volume removal. In-use data must fail closed.
    run('podman', 'volume', 'rm', app.volume('data'))
    return bootstrap_standby(app, primary_address, slot=app.replication_slot(rebuilt=True), **paths)
