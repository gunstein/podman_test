"""After group promotion: application tier, backup, standby rebuild and cluster status."""
import json

from . import standby, steps, trust
from .steps import app_installer


def require_identity(host):
    hostname = host.run(['hostname']).stdout.strip()
    addresses = steps.preflight_addresses(host.run(['ip', '-4', '-o', 'address', 'show', 'scope', 'global']).stdout)
    if hostname != host.name or host.spec.address not in addresses:
        raise RuntimeError(f'{host.name}: host identity or address does not match the inventory')


def deploy_promoted(project_root, controller, current):
    if not current.spec.local:
        raise RuntimeError('deploy-promoted-application runs on the promoted host itself: mark it local: true')
    pythonpath = trust.stage_installer(project_root, controller, current)
    p = steps.paths(current)
    return steps.changed(app_installer(
        current, pythonpath, 'deploy-promoted', '--project-root', str(project_root), '--quadlet-dir', p['quadlet'],
        '--bundle-dir', p['bundle'], '--inventory-hostname', current.name, '--node-address', current.spec.address,
        '--service-port', '8443', '--journal', p['config'] + '/promotion.json', '--config-dir', p['config']))


def configure_backup(project_root, controller, current):
    pythonpath = trust.stage_installer(project_root, controller, current)
    journal = steps.paths(current)['config'] + '/promotion.json'
    app_installer(current, pythonpath, 'require-promoted-group', '--journal', journal)
    changed = trust.install_trusted(
        project_root, controller, current,
        [(f'{project_root}/deploy/scripts/app_backup.py', '/opt/todo/bin/app_backup.py', '0644')], '/opt/todo/bin')
    result = current.run(['env', 'PYTHONDONTWRITEBYTECODE=1', 'python3', '/opt/todo/bin/app_backup.py',
                          'configure', '--journal', journal])
    return steps.changed(result) or changed


def preflight_rebuild(project_root, controller, current, rebuild, confirm_fenced, confirm_reseed):
    """Read-only gates on both hosts; the reseed itself repeats every rebuild-host check."""
    require_identity(current)
    current_path = trust.stage_installer(project_root, controller, current)
    for entry in steps.GROUP:
        app_installer(current, current_path, 'replicate-workload', 'rebuild-primary-check', '--app', entry['name'])
    require_identity(rebuild)
    if confirm_fenced != f'{rebuild.name} is fenced' or confirm_reseed != rebuild.name:
        raise RuntimeError('Rebuild host must remain infrastructure-fenced and both exact confirmations are '
                           'required before any destructive reseed.')
    rebuild_path = steps.stage_postgres_group(project_root, controller, rebuild)
    app_installer(rebuild, rebuild_path, 'replicate-workload', 'quarantined', '--app', steps.GROUP[0]['name'])
    for entry in steps.GROUP:
        app_installer(rebuild, rebuild_path, 'replicate-workload', 'reseed-check', '--app', entry['name'],
                      '--primary-address', current.spec.address, '--confirm-fenced', confirm_fenced,
                      '--confirm-reseed', confirm_reseed, *steps.group_paths(rebuild))
    return rebuild_path


def rebuild(project_root, controller, current, rebuild_host, confirm_fenced, confirm_reseed):
    for host in (controller, rebuild_host):
        host.run(['true'], sudo=True)
    rebuild_path = preflight_rebuild(project_root, controller, current, rebuild_host, confirm_fenced, confirm_reseed)
    current_path = steps.stage_postgres_group(project_root, controller, current)
    app_installer(current, current_path, 'publish-primaries', 'redundancy',
                  '--node-address', current.spec.address, *steps.group_paths(current))
    app_installer(rebuild_host, rebuild_path, 'reseed-group', '--primary-address', current.spec.address,
                  '--confirm-fenced', confirm_fenced, '--confirm-reseed', confirm_reseed,
                  *steps.group_paths(rebuild_host))
    standby.install_dr_tool(project_root, controller, rebuild_host, current.spec)
    standby.streaming(current, current_path, rebuilt=True)
    return True


def cluster_status(current, standby_host):
    report = {'changed': False}
    for role, host in (('primary', current), ('standby', standby_host)):
        report[role] = json.loads(app_installer(host, steps.installed_pythonpath(host),
                                                'cluster-status', role).stdout)['status']
    return report
