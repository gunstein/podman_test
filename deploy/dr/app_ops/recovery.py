"""After group promotion: application tier, backup, standby rebuild and cluster status; and a standby re-seed."""
import json

from . import standby, steps, trust
from .steps import app_dr_host, settings


def require_identity(host):
    """Raise unless the host reports the inventory hostname and owns the inventory address."""
    hostname = host.run(['hostname']).stdout.strip()
    addresses = steps.preflight_addresses(host.run(['ip', '-4', '-o', 'address', 'show', 'scope', 'global']).stdout)
    if hostname != host.name or host.spec.address not in addresses:
        raise RuntimeError(f'{host.name}: host identity or address does not match the inventory')


def deploy_promoted(project_root, controller, current):
    """Start the apps, Keycloak and nginx on the promoted host, which must be this machine.

    Uses the images from the offline bundle on that host and the operations
    package's target files, filled in with its recorded public hostnames.
    """
    if not current.spec.local:
        raise RuntimeError('deploy-promoted-application runs on the promoted host itself: mark it local: true')
    pythonpath = steps.stage_target_files(project_root, controller, current)
    p = steps.paths(current)
    return steps.changed(app_dr_host(
        current, pythonpath, 'deploy-promoted', '--quadlet-dir', p['quadlet'],
        '--bundle-dir', p['bundle'], '--inventory-hostname', current.name, '--node-address', current.spec.address,
        '--service-port', str(settings.HTTPS_PORT), '--journal', steps.promotion_record(current),
        '--config-dir', p['config']))


def configure_backup(project_root, controller, current):
    """Install app_backup.py on the current primary, turn on WAL archiving and the nightly backup.

    Refuses unless the promotion record shows the whole group was promoted.
    platform-backup.timer then runs `app_backup.py nightly` every night; on a host
    installed with install.sh it replaces the installer's service of the same timer.
    """
    pythonpath = steps.stage_target_files(project_root, controller, current)
    journal = steps.promotion_record(current)
    app_dr_host(current, pythonpath, 'require-promoted-group', '--journal', journal)
    changed = trust.install_trusted(
        project_root, controller, current,
        [(f'{project_root}/deploy/dr/scripts/app_backup.py', settings.TOOLS_BIN / 'app_backup.py', '0644')],
        settings.TOOLS_BIN)
    result = current.run(['env', 'PYTHONDONTWRITEBYTECODE=1', 'python3', str(settings.TOOLS_BIN / 'app_backup.py'),
                          'configure', '--journal', journal], timeout=steps.STEP_TIMEOUT)
    changed = steps.changed(result) or changed
    return steps.install_timer(project_root, current, 'platform-backup') or changed


def preflight_rebuild(project_root, controller, current, rebuild, confirm_fenced, confirm_reseed):
    """Read-only gates on both hosts; the reseed itself repeats every rebuild-host check."""
    # The confirmations first: a wrong one stops the run before anything is staged on either host.
    if confirm_fenced != f'{rebuild.name} is fenced' or confirm_reseed != rebuild.name:
        raise RuntimeError('Rebuild host must remain infrastructure-fenced and both exact confirmations are '
                           'required before any destructive reseed.')
    group = steps.platform(project_root).replicated_databases
    require_identity(current)
    current_path = steps.stage_target_files(project_root, controller, current)
    for database in group:
        app_dr_host(current, current_path, 'replicate-workload', 'rebuild-primary-check', '--app', database.name)
    require_identity(rebuild)
    # The current primary's public hostnames, which the rebuilt standby will serve.
    rebuild_path = steps.stage_target_files(project_root, controller, rebuild)
    app_dr_host(rebuild, rebuild_path, 'replicate-workload', 'quarantined', '--app', group[0].name)
    hostnames = steps.target_values(current, current_path)
    for database in group:
        app_dr_host(rebuild, rebuild_path, 'replicate-workload', 'reseed-check', '--app', database.name,
                      '--primary-address', current.spec.address, '--confirm-fenced', confirm_fenced,
                      '--confirm-reseed', confirm_reseed, '--node-address', rebuild.spec.address,
                      '--target-values', hostnames, *steps.group_paths(rebuild))
    return rebuild_path


def rebuild(project_root, controller, current, rebuild_host, confirm_fenced, confirm_reseed):
    """Rebuild the old primary as a standby of the current one. Deletes its database data.

    Order: check passwordless sudo on both hosts, run every preflight gate,
    publish the current primary's databases for the rebuilt standby, copy
    missing DR secrets (the replication CA) to the rebuild host, require
    a connection from the rebuild host to every replication port, reseed
    every database on the rebuild host, install app_dr.py and its check
    timer there, and wait
    until all of them stream. A failure stops the run where it is and is
    never retried automatically.
    """
    group = steps.platform(project_root).replicated_databases
    for host in (controller, rebuild_host):
        host.run(['true'], sudo=True)
    rebuild_path = preflight_rebuild(project_root, controller, current, rebuild_host, confirm_fenced, confirm_reseed)
    current_path = steps.stage_target_files(project_root, controller, current)
    app_dr_host(current, current_path, 'publish-primaries', 'redundancy',
                  '--node-address', current.spec.address, *steps.group_paths(current))
    # The rebuilt standby serves the current primary's public hostnames.
    hostnames = steps.target_values(current, current_path)
    # The rebuild host needs the replication CA that publishing may just have
    # created (a pair set up before replication TLS); existing values must match.
    standby.sync_secrets(project_root, controller, current, rebuild_host)
    for database in group:
        app_dr_host(rebuild_host, rebuild_path, 'replicate-workload', 'replication-path', '--app', database.name,
                      '--primary-address', current.spec.address)
    app_dr_host(rebuild_host, rebuild_path, 'reseed-group', '--primary-address', current.spec.address,
                  '--confirm-fenced', confirm_fenced, '--confirm-reseed', confirm_reseed,
                  '--node-address', rebuild_host.spec.address, '--target-values', hostnames,
                  *steps.group_paths(rebuild_host), timeout=steps.copy_step_timeout(group))
    standby.install_dr_tool(project_root, controller, rebuild_host, current.spec, rebuild_host.name)
    standby.streaming(group, current, current_path, rebuilt=True)
    return True


def reseed_standby(project_root, controller, primary, standby_host, confirm_reseed):
    """Copy every database to the standby again from the primary, without a failover. Deletes the standby's copy.

    For a standby that lost its slot (backlog D10), or any time its copy must
    be taken again; the primary keeps serving throughout. Order: both hosts
    match the inventory, the primary's firewall rule is there, each
    database's one slot is found on the primary, the standby proves it is a
    read-only, database-only standby that reaches and logs in to the
    primary, and only then does it stop and erase its databases. The primary
    drops each idle slot, the standby copies every database again with the
    same slot name, its DR check is installed again, and the run ends when all
    of them stream. A failure stops where it is; after the erase, the primary
    is untouched and bootstrap-standby builds the standby again.
    """
    if confirm_reseed != standby_host.name:
        raise RuntimeError(f'--confirm-reseed must name the standby exactly ({standby_host.name}); '
                           'nothing was changed')
    group = steps.platform(project_root).replicated_databases
    for host in (primary, standby_host):
        require_identity(host)
    standby.require_firewall(group, primary, standby_host)
    primary_path = steps.stage_target_files(project_root, controller, primary)
    slots = {database.name: json.loads(app_dr_host(primary, primary_path, 'replicate-workload', 'slot',
                                                   '--app', database.name).stdout)['slot']
             for database in group}
    hostnames = steps.target_values(primary, primary_path)
    standby.sync_secrets(project_root, controller, primary, standby_host)
    standby_path = steps.stage_target_files(project_root, controller, standby_host)
    app_dr_host(standby_host, standby_path, 'standby-reseed-check', '--primary-address', primary.spec.address)
    app_dr_host(standby_host, standby_path, 'erase-standby', '--primary-address', primary.spec.address,
                '--confirm-reseed', confirm_reseed)
    for database in group:
        # The standby's WAL senders end a moment after its databases stop.
        steps.retry(lambda database=database: app_dr_host(
            primary, primary_path, 'replicate-workload', 'drop-slot', '--app', database.name,
            '--slot', slots[database.name]), 15, 2)
    images = steps.paths(standby_host)['bundle'] + '/images/'
    for database in group:
        app_dr_host(standby_host, standby_path, 'replicate-workload', 'standby', '--app', database.name,
                    '--primary-address', primary.spec.address, '--slot', slots[database.name],
                    '--image-archive', images + database.image_archive,
                    '--node-address', standby_host.spec.address, '--target-values', hostnames,
                    *steps.group_paths(standby_host), timeout=steps.copy_step_timeout(group))
    standby.install_dr_tool(project_root, controller, standby_host, primary.spec, standby_host.name)
    for database in group:
        steps.retry(lambda database=database: app_dr_host(
            primary, primary_path, 'replicate-workload', 'streaming', '--app', database.name,
            '--slot', slots[database.name]), 15, 2)
    return True


def cluster_status(current, standby_host):
    """Read-only report of both hosts: the primary must stream and archive, the standby must replay."""
    report = {'changed': False}
    for role, host in (('primary', current), ('standby', standby_host)):
        report[role] = json.loads(app_dr_host(host, steps.installed_pythonpath(host),
                                                'cluster-status', role).stdout)['status']
    return report
