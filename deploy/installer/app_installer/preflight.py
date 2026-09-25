"""Read-only checks of both initial database hosts before standby bootstrap."""
from . import apps
from .commands import exists, run


def ipv4_addresses(ip_output):
    """Addresses from `ip -4 -o address show`, without prefix lengths."""
    addresses = []
    for line in ip_output.splitlines():
        fields = line.split()
        if 'inet' in fields[:-1]:
            addresses.append(fields[fields.index('inet') + 1].split('/')[0])
    return addresses


def node_facts(inventory_hostname, role, address):
    """This host's identity and database volumes; refuses an inventory it does not match."""
    run('podman', '--version')
    run('systemctl', '--user', 'is-system-running', allowed=(0, 1))
    facts = {
        'inventory_hostname': inventory_hostname,
        'role': role,
        'address': address,
        'hostname': run('hostname').stdout.strip(),
        'machine_id': run('cat', '/etc/machine-id').stdout.strip(),
        'ipv4_addresses': ipv4_addresses(run('ip', '-4', '-o', 'address', 'show', 'scope', 'global').stdout),
        'data_volumes': {database.volume('data'): exists('volume', database.volume('data'))
                         for database in apps.REPLICATED_DATABASES},
    }
    problems = []
    if role not in ('primary', 'standby'):
        problems.append(f'role {role!r} is neither primary nor standby')
    if facts['hostname'] != inventory_hostname:
        problems.append(f'hostname {facts["hostname"]!r} is not the inventory name {inventory_hostname!r}')
    if address not in facts['ipv4_addresses']:
        problems.append(f'{address} is not a global IPv4 address on this host')
    if len(facts['machine_id']) != 32:
        problems.append('/etc/machine-id does not hold a 32-character machine ID')
    if problems:
        raise RuntimeError('Host identity does not match the initial-topology inventory: '
                           + '; '.join(problems) + '. Check hostname, todo_node_address and '
                           '/etc/machine-id before continuing.')
    return facts


def check_pair(primary, standby):
    """Every cross-host rule, all reported together; nothing here changes either host."""
    expected = sorted(database.volume('data') for database in apps.REPLICATED_DATABASES)
    problems = []
    if primary['role'] != 'primary' or standby['role'] != 'standby':
        problems.append('the first host must have role primary and the second role standby')
    if primary['inventory_hostname'] == standby['inventory_hostname']:
        problems.append('primary and standby are the same inventory host')
    if primary['machine_id'] == standby['machine_id']:
        problems.append('primary and standby share a machine ID (a cloned VM needs a new /etc/machine-id)')
    if primary['address'] == standby['address']:
        problems.append('primary and standby have the same address')
    for name, host in (('primary', primary), ('standby', standby)):
        if sorted(host['data_volumes']) != expected:
            problems.append(f'{name} reported a different database group than {", ".join(expected)}')
    missing = [volume for volume, present in primary['data_volumes'].items() if not present]
    if missing:
        problems.append('every registered primary database must already have its data volume; '
                        'missing: ' + ', '.join(missing))
    existing = [volume for volume, present in standby['data_volumes'].items() if present]
    if existing:
        problems.append('standby already has registered database data volumes (' + ', '.join(existing)
                        + '); the preflight will not overwrite them. Inspect or remove them explicitly '
                        'before bootstrap')
    if problems:
        raise RuntimeError('Standby preflight failed: ' + '; '.join(problems)
                           + '. No runtime or database state was changed.')
