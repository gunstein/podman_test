"""Initial topology: standby preflight, secrets, bootstrap, replication status and the DR tool."""
import json

from . import steps, trust


def firewall_rule(primary, standby):
    """The firewalld rich rule that lets only the standby reach the primary's replication ports."""
    ports = [entry['replication_port'] for entry in steps.GROUP]
    return (f'rule family="ipv4" source address="{standby.spec.address}/32" '
            f'destination address="{primary.spec.address}" port port="{min(ports)}-{max(ports)}" '
            'protocol="tcp" accept')


def interface_of(ip_output, address):
    """The interface that holds address, from 'ip -4 -o address show' output; '' if none does."""
    for line in ip_output.splitlines():
        fields = line.split()
        if 'inet' in fields[:-1] and fields[fields.index('inet') + 1].split('/')[0] == address:
            return fields[1]
    return ''


def require_firewall(primary, standby):
    """The primary publishes PostgreSQL on the LAN only behind the documented rich rule.

    The rule must be in the zone of the interface that holds the primary's
    address, and in both the running and the permanent configuration: a rule
    only in the permanent configuration does not apply until a reload, and a
    rule in another zone never applies to this interface.
    """
    primary.run(['firewall-cmd', '--state'], sudo=True)
    interface = interface_of(primary.run(['ip', '-4', '-o', 'address', 'show', 'scope', 'global']).stdout,
                             primary.spec.address)
    if not interface:
        raise RuntimeError(f'{primary.name}: no interface holds {primary.spec.address}')
    zone = primary.run(['firewall-cmd', f'--get-zone-of-interface={interface}'], sudo=True,
                       allowed=(0, 2)).stdout.strip()
    if not zone or ' ' in zone:
        zone = primary.run(['firewall-cmd', '--get-default-zone'], sudo=True).stdout.strip()
    rule = firewall_rule(primary, standby)
    missing = [where for where, flags in (('running', []), ('permanent', ['--permanent']))
               if primary.run(['firewall-cmd', *flags, f'--zone={zone}', f'--query-rich-rule={rule}'],
                              sudo=True, allowed=(0, 1)).returncode]
    if missing:
        raise RuntimeError(
            f'The firewalld zone {zone} of {interface} on {primary.name} has no rich rule in its '
            f'{" and ".join(missing)} configuration that permits {standby.spec.address} to reach the '
            'PostgreSQL replication ports. Add it before bootstrap publishes them on the LAN interface, '
            f"then reload: sudo firewall-cmd --permanent --zone={zone} --add-rich-rule='{rule}' && "
            'sudo firewall-cmd --reload')


def preflight(project_root, controller, primary, standby):
    """Read-only checks of both hosts before a standby is bootstrapped.

    Each host must match the inventory (hostname, address, machine ID); the
    primary needs the firewall rule; then the pair is checked together: the
    primary has every database, and the standby has none yet.
    """
    facts = {}
    for role, host in (('primary', primary), ('standby', standby)):
        pythonpath = trust.stage_installer(project_root, controller, host)
        facts[role] = steps.app_installer(host, pythonpath, 'node-facts', '--inventory-hostname', host.name,
                                          '--role', role, '--address', host.spec.address).stdout.strip()
        if role == 'primary':
            require_firewall(primary, standby)
            primary_pythonpath = pythonpath
    steps.app_installer(primary, primary_pythonpath, 'check-standby-pair', facts['primary'], facts['standby'])
    return False


def sync_secrets(project_root, controller, primary, standby):
    """Copy the replication group's credentials from the primary to the standby.

    The export goes straight from one command's stdout to the other's stdin,
    in memory. An existing secret with a different value stops the import.
    """
    primary_path = trust.stage_installer(project_root, controller, primary)
    transfer = steps.app_installer(primary, primary_path, 'export-replication-secrets').stdout
    standby_path = trust.stage_installer(project_root, controller, standby)
    return steps.changed(steps.app_installer(standby, standby_path, 'import-replication-secrets', input=transfer))


def streaming(primary, pythonpath, *, rebuilt=False):
    """Wait until every database streams to its standby: 15 tries, 2 seconds apart."""
    for entry in steps.GROUP:
        steps.retry(lambda entry=entry: steps.app_installer(
            primary, pythonpath, 'replicate-workload', 'streaming', '--app', entry['name'],
            *(['--rebuilt'] if rebuilt else [])), 15, 2)


def bootstrap(project_root, controller, primary, standby):
    """Build the standby: preflight, publish the primaries, copy secrets, then base backups.

    The primary's databases are published on the LAN and get their
    replicator role first. The standby then gets a base backup of each
    database, and the run ends when all of them stream.
    """
    preflight(project_root, controller, primary, standby)
    primary_path = steps.stage_postgres_group(project_root, controller, primary)
    changed = steps.changed(steps.app_installer(primary, primary_path, 'publish-primaries', 'bootstrap',
                                                '--node-address', primary.spec.address, *steps.group_paths(primary)))
    changed = sync_secrets(project_root, controller, primary, standby) or changed
    standby_path = steps.stage_postgres_group(project_root, controller, standby)
    images = steps.paths(standby)['bundle'] + '/images/'
    for entry in steps.GROUP:
        changed = steps.changed(steps.app_installer(
            standby, standby_path, 'replicate-workload', 'standby', '--app', entry['name'],
            '--primary-address', primary.spec.address, '--image-archive', images + entry['postgres_archive'],
            *steps.group_paths(standby))) or changed
    streaming(primary, primary_path)
    return changed


def replication_status(project_root, controller, primary, standby):
    """Raise unless every database streams and every standby database is read-only."""
    primary_path = trust.stage_installer(project_root, controller, primary)
    streaming(primary, primary_path)
    standby_path = trust.stage_installer(project_root, controller, standby)
    for entry in steps.GROUP:
        state = json.loads(steps.app_installer(standby, standby_path, 'replicate-workload', 'status',
                                               '--app', entry['name']).stdout)['status']
        if not (state['in_recovery'] and state['transaction_read_only']):
            raise RuntimeError(f'{entry["name"]}: a member of the standby group is writable')
    return False


def install_dr_tool(project_root, controller, standby, primary_spec):
    """Install app_dr.py on the standby with exact-file trust, then write its DR settings."""
    trust.stage_installer(project_root, controller, standby)
    changed = trust.install_trusted(project_root, controller, standby,
                                    [(f'{project_root}/deploy/scripts/app_dr.py', '/opt/todo/bin/app_dr.py', '0644')],
                                    '/opt/todo/bin')
    result = standby.run(['env', 'PYTHONDONTWRITEBYTECODE=1', 'python3', '/opt/todo/bin/app_dr.py', '--config',
                          steps.paths(standby)['config'] + '/todo-dr.json', 'configure',
                          '--primary-name', primary_spec.name, '--primary-address', primary_spec.address,
                          '--standby-name', standby.name, '--rpo-target-seconds', '30'])
    return steps.changed(result) or changed
