"""Initial topology: standby preflight, secrets, bootstrap, replication status and the DR tool."""
import json

from . import steps, trust


def firewall_rule(primary, standby):
    ports = [entry['replication_port'] for entry in steps.GROUP]
    return (f'rule family="ipv4" source address="{standby.spec.address}/32" '
            f'destination address="{primary.spec.address}" port port="{min(ports)}-{max(ports)}" '
            'protocol="tcp" accept')


def require_firewall(primary, standby):
    """The primary publishes PostgreSQL on the LAN only behind the documented rich rule."""
    primary.run(['firewall-cmd', '--state'], sudo=True)
    rule = firewall_rule(primary, standby)
    query = primary.run(['firewall-cmd', '--permanent', '--zone=public', f'--query-rich-rule={rule}'],
                        sudo=True, allowed=(0, 1))
    if query.returncode:
        raise RuntimeError(
            f'No firewalld rich rule on {primary.name} permits {standby.spec.address} to reach the PostgreSQL '
            'replication ports. Add it before bootstrap publishes them on the LAN interface, then reload: '
            f"sudo firewall-cmd --permanent --zone=public --add-rich-rule='{rule}' && sudo firewall-cmd --reload")


def preflight(project_root, controller, primary, standby):
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
    primary_path = trust.stage_installer(project_root, controller, primary)
    transfer = steps.app_installer(primary, primary_path, 'export-replication-secrets').stdout
    standby_path = trust.stage_installer(project_root, controller, standby)
    return steps.changed(steps.app_installer(standby, standby_path, 'import-replication-secrets', input=transfer))


def streaming(primary, pythonpath, *, rebuilt=False):
    for entry in steps.GROUP:
        steps.retry(lambda entry=entry: steps.app_installer(
            primary, pythonpath, 'replicate-workload', 'streaming', '--app', entry['name'],
            *(['--rebuilt'] if rebuilt else [])), 15, 2)


def bootstrap(project_root, controller, primary, standby):
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
    trust.stage_installer(project_root, controller, standby)
    changed = trust.install_trusted(project_root, controller, standby,
                                    [(f'{project_root}/deploy/scripts/app_dr.py', '/opt/todo/bin/app_dr.py', '0644')],
                                    '/opt/todo/bin')
    result = standby.run(['env', 'PYTHONDONTWRITEBYTECODE=1', 'python3', '/opt/todo/bin/app_dr.py', '--config',
                          steps.paths(standby)['config'] + '/todo-dr.json', 'configure',
                          '--primary-name', primary_spec.name, '--primary-address', primary_spec.address,
                          '--standby-name', standby.name, '--rpo-target-seconds', '30'])
    return steps.changed(result) or changed
