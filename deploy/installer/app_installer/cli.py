"""Command line interface; workload commands emit exactly one JSON result."""
import argparse
import json
import sys
from pathlib import Path

from . import (
    apps,
    backup,
    install,
    kube_play,
    oplog,
    settings,
    target_render,
    tls_secrets,
    tls_store,
    uninstall,
)

try:  # Jinja2 renders on a build host; an offline host installs without it.
    from jinja2 import TemplateError
    RENDER_ERRORS: tuple = (TemplateError,)
except ImportError:
    RENDER_ERRORS = ()


def paths(parser):
    """Add the options every installing command shares: project root, Quadlet and Kube runtime directories."""
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--quadlet-dir', type=Path, default=settings.QUADLET_DIR)
    parser.add_argument('--kube-runtime-dir', type=Path)


def _details(database):
    """One database's names as the table in docs/ACCEPTANCE.md lists them, for comparing the two."""
    return {'name': database.name, 'container': database.container,
            'service': database.service, 'replication_port': database.replication_port,
            'standby_slot': database.replication_slot(), 'rebuilt_slot': database.replication_slot(rebuilt=True)}


def target_hostnames(entries):
    """--target-hostname NAME=HOSTNAME options as target values: {TARGET_..._HOSTNAME: hostname}.

    NAME is identity (Keycloak) or an app's name; the bundle's platform
    decides which names it knows (target_render.load).
    """
    values = {}
    for entry in entries or ():
        name, separator, hostname = entry.partition('=')
        if not separator or not name or not hostname:
            raise ValueError(f'--target-hostname takes NAME=HOSTNAME, not {entry!r}')
        values[target_render.name_target(name)] = hostname
    return values


def backup_command(args):
    """backup create | nightly | restore; text for the journal, problems as ERROR lines, exit 1 on any."""
    platform = target_render.installed_platform()
    if args.backup_command == 'create':
        for database in backup.installed_databases(platform):
            print(f'{database.name}: verified base backup {backup.create(database)}')
        return 0
    if args.backup_command == 'nightly':
        if args.keep_days < 1:
            raise ValueError('--keep-days must keep at least one day')
        lines, problems = backup.nightly(platform, args.keep_days)
        # The same nightly run looks at nginx's certificate (tls_store: secrets or volume).
        tls_lines, tls_problems = tls_check()
        lines, problems = lines + tls_lines, problems + tls_problems
        print('\n'.join(lines))
        for problem in problems:
            print(f'ERROR: {problem}', file=sys.stderr)
        return 1 if problems else 0
    install.require_single_host('backup restore', platform)
    restored = backup.restore(platform, args.confirm_restore)
    for name, chosen in restored.items():
        print(f'{name}: restored {chosen}')
    print(json.dumps({'changed': True}))
    return 0


def tls_check():
    """check() of the TLS storage in use, with a failure to look reported as a problem rather than raised."""
    try:
        return tls_store.module().check()
    except (OSError, RuntimeError, ValueError) as error:
        return [], [f'cannot check the nginx certificate: {error}']


def tls_command(args):
    """tls-request | tls-install | tls-status | tls-renew: nginx's certificate (tls_secrets.py or tls.py)."""
    tls = tls_store.module()
    if args.command == 'tls-renew':
        if not tls_store.secret_storage():
            raise ValueError('tls-renew is for Podman secrets; with the TLS volume, '
                             'systemctl --user restart shared-proxy.service renews the demo certificate')
        print(json.dumps({'changed': tls_secrets.renew()}))
        return 0
    if args.command == 'tls-request':
        names = tls.request(args.output, args.new_key)
        print(f'Have the CA sign {args.output} (app_ca.py sign, or sudo platform-ca-sign), then: '
              'python3 -m app_installer tls-install --certificate FILE --ca FILE', file=sys.stderr)
        print(json.dumps({'changed': True, 'request': str(args.output), 'hostnames': names}))
        return 0
    if args.command == 'tls-install':
        changed = tls.install(args.certificate, args.ca)
        print(json.dumps({'changed': changed}))
        return 0
    lines, problems = tls_check()
    print('\n'.join(lines or ['nginx has no certificate yet']))
    for problem in problems:
        print(f'ERROR: {problem}', file=sys.stderr)
    return 1 if problems else 0


def main(argv=None):
    """Parse one subcommand, run it, and return the exit code.

    install, uninstall, down, backup and the tls- commands serve a single host
    (tls-request, tls-install, tls-status and tls-renew touch only nginx's
    TLS files, so they also run on a DR host); replication-apps
    prints the registry's DR group for the acceptance guide to compare with
    its table. install takes its platform from the bundle or the registry;
    the other host commands take the one the host recorded at install.
    The DR tools import the installer's functions instead, and their own
    commands live in app_dr_host (deploy/dr). Each command prints one JSON
    result on stdout (backup prints lines for the journal). Errors print one
    "app-installer: ..." line on stderr and return 1.
    """
    parser = argparse.ArgumentParser(description='Rootless Podman Todo installer')
    subcommands = parser.add_subparsers(dest='command', required=True)
    deploy = subcommands.add_parser('install')
    paths(deploy)
    deploy.add_argument('--mode', choices=('dev', 'server'), default='server')
    deploy.add_argument('--deployment-mode', choices=('build', 'offline'), default='build')
    deploy.add_argument('--bundle-dir', default='')
    deploy.add_argument('--refresh-images', action='store_true')
    deploy.add_argument('--publish-address', default='127.0.0.1')
    deploy.add_argument('--service-port', type=int, default=settings.HTTPS_PORT)
    deploy.add_argument('--target-hostname', action='append', metavar='NAME=HOSTNAME',
                        help='a public hostname for an offline bundle: NAME is identity (Keycloak) or an app '
                             'of the bundle; repeat it for each (see target_render.py)')
    registry = subcommands.add_parser('replication-apps')
    registry.add_argument('--details', action='store_true')
    remove = subcommands.add_parser('uninstall')
    remove.add_argument('--remove-data', action='store_true')
    remove.add_argument('--remove-backups', action='store_true',
                        help='with --remove-data: the backup volumes too; nothing can be restored afterwards')
    remove.add_argument('--quadlet-dir', type=Path)
    backups = subcommands.add_parser('backup', help='nightly base backups of this host (platform-backup.timer)')
    backup_commands = backups.add_subparsers(dest='backup_command', required=True)
    backup_commands.add_parser('create', help='a verified base backup of every installed database')
    nightly = backup_commands.add_parser('nightly', help='create, then delete backups older than --keep-days')
    nightly.add_argument('--keep-days', type=int, required=True)
    restore = backup_commands.add_parser('restore', help='put every database back to its latest backup')
    restore.add_argument('--confirm-restore', required=True, help="exactly this host's name")
    tls_request = subcommands.add_parser('tls-request', help="a key and CSR for nginx's certificate (provided mode)")
    tls_request.add_argument('--output', type=Path, required=True, help='where to write the CSR')
    tls_request.add_argument('--new-key', action='store_true', help='replace a key that already waits')
    tls_install = subcommands.add_parser('tls-install', help='check and use a certificate the CA signed')
    tls_install.add_argument('--certificate', type=Path, required=True,
                             help='the server certificate, then any intermediate CAs (PEM)')
    tls_install.add_argument('--ca', type=Path, required=True, help="the organisation's root CA (PEM)")
    subcommands.add_parser('tls-status', help="nginx's TLS mode and how long its certificate lasts")
    subcommands.add_parser('tls-renew', help="renew nginx's demo certificate if it is due, then restart nginx")
    down = subcommands.add_parser('down')
    down.add_argument('--rendered-manifest-dir', type=Path,
                      default=Path(__file__).resolve().parents[3] / 'generated/dev')
    args = parser.parse_args(argv)
    oplog.describe(args.command, getattr(args, 'backup_command', None))
    try:
        if args.command == 'replication-apps':
            print(json.dumps([_details(d) if args.details else d.name
                              for d in apps.registry().replicated_databases]))
        elif args.command == 'install':
            changed = install.install(
                args.project_root, args.mode, args.deployment_mode, args.bundle_dir, args.refresh_images,
                args.publish_address, args.service_port, args.quadlet_dir, args.kube_runtime_dir,
                target_values=target_hostnames(args.target_hostname))
            print(json.dumps({'changed': changed}))
        elif args.command == 'uninstall':
            if args.remove_backups and not args.remove_data:
                parser.error('--remove-backups needs --remove-data')
            platform = target_render.installed_platform()
            changed = uninstall.uninstall(platform, args.remove_data, args.quadlet_dir, args.remove_backups)
            if args.remove_backups:
                print(f'Backup volumes {", ".join(uninstall.backup_volumes(platform))} were removed: '
                      'nothing of this install can be restored any more.', file=sys.stderr)
            elif args.remove_data:
                print(f'Backup volumes {", ".join(uninstall.backup_volumes(platform))} were preserved. '
                      'Use --remove-backups too to delete them permanently.', file=sys.stderr)
            else:
                volumes = ', '.join(d.volume('data') for d in platform.replicated_databases)
                tls_volumes = ', '.join(uninstall.TLS_VOLUMES)
                print(f'Database volumes {volumes}, TLS volumes {tls_volumes} and database, Keycloak and '
                      'nginx TLS secrets were preserved. Use --remove-data to delete them permanently.',
                      file=sys.stderr)
            print(json.dumps({'changed': changed}))
        elif args.command == 'backup':
            return backup_command(args)
        elif args.command.startswith('tls-'):
            return tls_command(args)
        elif args.command == 'down':
            try:
                platform = target_render.installed_platform()
            except target_render.TargetError:
                platform = None
            if platform is None or not kube_play.down(args.rendered_manifest_dir, platform):
                print('No installed development manifests were found under '
                      f'{args.rendered_manifest_dir}; nothing was torn down.', file=sys.stderr)
    except (OSError, RuntimeError, ValueError, KeyError, *RENDER_ERRORS) as error:
        print(f'app-installer: {error}', file=sys.stderr)
        return 1
    return 0
