"""Command line interface; workload commands emit exactly one JSON result."""
import argparse
import json
import sys
from pathlib import Path

from . import apps, backup, install, kube_play, settings, target_render, uninstall, workloads

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


def backup_command(args):
    """backup create | nightly | restore; text for the journal, problems as ERROR lines, exit 1 on any."""
    if args.backup_command == 'create':
        for database in backup.installed_databases():
            print(f'{database.name}: verified base backup {backup.create(database)}')
        return 0
    if args.backup_command == 'nightly':
        if args.keep_days < 1:
            raise ValueError('--keep-days must keep at least one day')
        lines, problems = backup.nightly(args.keep_days)
        print('\n'.join(lines))
        for problem in problems:
            print(f'ERROR: {problem}', file=sys.stderr)
        return 1 if problems else 0
    install.require_single_host('backup restore')
    restored = backup.restore(args.confirm_restore)
    for name, chosen in restored.items():
        print(f'{name}: restored {chosen}')
    print(json.dumps({'changed': True}))
    return 0


def main(argv=None):
    """Parse one subcommand, run it, and return the exit code.

    install, uninstall and down serve a single host; install-workload,
    configure-clients and the registry commands are also used by the DR tools,
    whose own commands live in app_dr_host (deploy/dr). Each prints one JSON
    result on stdout. Errors print one "app-installer: ..." line on stderr and
    return 1.
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
    for app in apps.APPS:  # --target-external-hostname, --target-notes-hostname
        option = target_render.hostname_target(app).removeprefix('TARGET_').lower().replace('_', '-')
        deploy.add_argument(f'--target-{option}', dest=target_render.hostname_target(app), default=None,
                            help=f'public hostname of {app.name} for an offline bundle (see target_render.py)')
    workload = subcommands.add_parser('install-workload')
    paths(workload)
    workload.add_argument('workload', choices=('postgres', 'application', 'keycloak', 'shared-proxy'))
    workload.add_argument('--app', choices=[app.name for app in apps.APPS] + [apps.KEYCLOAK_DATABASE.name],
                          default=apps.SHARED_RESOURCE_OWNER.name)
    workload.add_argument('--rendered-manifest-dir', type=Path)
    workload.add_argument('--publish-address', default='127.0.0.1')
    workload.add_argument('--postgres-publish-address', default='')
    workload.add_argument('--service-port', type=int, default=settings.HTTPS_PORT)
    registry = subcommands.add_parser('replication-apps')
    registry.add_argument('--details', action='store_true')
    subcommands.add_parser('configure-clients')
    service_list = subcommands.add_parser('services')
    service_list.add_argument('--application-tier', action='store_true')
    remove = subcommands.add_parser('uninstall')
    remove.add_argument('--remove-data', action='store_true')
    remove.add_argument('--quadlet-dir', type=Path)
    backups = subcommands.add_parser('backup', help='nightly base backups of this host (todo-backup.timer)')
    backup_commands = backups.add_subparsers(dest='backup_command', required=True)
    backup_commands.add_parser('create', help='a verified base backup of every installed database')
    nightly = backup_commands.add_parser('nightly', help='create, then delete backups older than --keep-days')
    nightly.add_argument('--keep-days', type=int, required=True)
    restore = backup_commands.add_parser('restore', help='put every database back to its latest backup')
    restore.add_argument('--confirm-restore', required=True, help="exactly this host's name")
    down = subcommands.add_parser('down')
    down.add_argument('--rendered-manifest-dir', type=Path,
                      default=Path(__file__).resolve().parents[3] / 'generated/dev')
    args = parser.parse_args(argv)
    try:
        if args.command == 'replication-apps':
            print(json.dumps([_details(d) if args.details else d.name for d in apps.REPLICATED_DATABASES]))
        elif args.command == 'configure-clients':
            from . import keycloak, secrets
            changed = keycloak.configure(secrets.read(apps.KEYCLOAK_ADMIN_SECRET),
                                         [(app.keycloak_client, app.hostname) for app in apps.APPS])
            print(json.dumps({'changed': changed}))
        elif args.command == 'services':
            print(json.dumps(apps.services(databases=not args.application_tier)))
        elif args.command == 'install':
            changed = install.install(
                args.project_root, args.mode, args.deployment_mode, args.bundle_dir, args.refresh_images,
                args.publish_address, args.service_port, args.quadlet_dir, args.kube_runtime_dir,
                target_values={name: getattr(args, name) for name in target_render.HOSTNAMES})
            print(json.dumps({'changed': changed}))
        elif args.command == 'uninstall':
            changed = uninstall.uninstall(args.remove_data, args.quadlet_dir)
            if not args.remove_data:
                volumes = ', '.join(d.volume('data') for d in apps.REPLICATED_DATABASES)
                tls_volumes = ', '.join(uninstall.TLS_VOLUMES)
                print(f'Database volumes {volumes}, TLS volumes {tls_volumes} and database and '
                      'Keycloak secrets were preserved. Use --remove-data to delete them permanently.',
                      file=sys.stderr)
            print(json.dumps({'changed': changed}))
        elif args.command == 'backup':
            return backup_command(args)
        elif args.command == 'down':
            if not kube_play.down(args.rendered_manifest_dir):
                print('No installed development manifests were found under '
                      f'{args.rendered_manifest_dir}; nothing was torn down.', file=sys.stderr)
        else:
            directory = args.quadlet_dir.resolve()
            install.preflight(directory)
            runtime = (args.kube_runtime_dir or directory / settings.KUBE_RUNTIME).resolve()
            manifests = args.rendered_manifest_dir or args.project_root / 'generated/kube-runtime'
            kwargs = {'publish_address': args.publish_address, 'service_port': args.service_port}
            if args.workload == 'postgres':
                kwargs = {'publish_address': args.postgres_publish_address}
            if args.workload == 'keycloak':
                kwargs = {}
            if args.app == apps.KEYCLOAK_DATABASE.name:
                if args.workload == 'application':
                    raise ValueError('--app keycloak has no application workload; use postgres or keycloak.')
                selected_app = apps.KEYCLOAK_DATABASE
            else:
                selected_app = next(app for app in apps.APPS if app.name == args.app)
            if args.workload == 'postgres':
                kwargs['database'] = getattr(selected_app, 'database', selected_app)
            elif args.workload == 'application':
                kwargs['app'] = selected_app
            elif selected_app != apps.SHARED_RESOURCE_OWNER:
                raise ValueError('--app selects a postgres or application workload only.')
            function = {'postgres': workloads.install_postgres,
                        'application': workloads.install_application,
                        'keycloak': workloads.install_keycloak,
                        'shared-proxy': workloads.install_shared_proxy}[args.workload]
            changed = function(args.project_root, directory, runtime, manifests, **kwargs)
            # The shared-resource owner's application call also stages the shared
            # Keycloak workload; another app's own database has no bearing on this.
            if args.workload == 'application' and selected_app == apps.SHARED_RESOURCE_OWNER:
                changed = workloads.install_keycloak(
                    args.project_root, directory, runtime, manifests) or changed
            print(json.dumps({'changed': changed}))
    except (OSError, RuntimeError, ValueError, KeyError, *RENDER_ERRORS) as error:
        print(f'app-installer: {error}', file=sys.stderr)
        return 1
    return 0
