"""Command line interface; workload commands emit exactly one JSON result."""
import argparse
import json
import sys
from pathlib import Path

from jinja2 import TemplateError

from . import apps, install, kube_play, uninstall, workloads


def paths(parser):
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--quadlet-dir', type=Path,
                        default=Path.home() / '.config/containers/systemd')
    parser.add_argument('--kube-runtime-dir', type=Path)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Rootless Podman Todo installer')
    subcommands = parser.add_subparsers(dest='command', required=True)
    deploy = subcommands.add_parser('install')
    paths(deploy)
    deploy.add_argument('--mode', choices=('dev', 'server'), default='server')
    deploy.add_argument('--deployment-mode', choices=('build', 'offline'), default='build')
    deploy.add_argument('--bundle-dir', default='')
    deploy.add_argument('--refresh-images', action='store_true')
    deploy.add_argument('--publish-address', default='127.0.0.1')
    deploy.add_argument('--service-port', type=int, default=8443)
    workload = subcommands.add_parser('install-workload')
    paths(workload)
    workload.add_argument('workload', choices=('postgres', 'application', 'keycloak', 'shared-proxy'))
    workload.add_argument('--app', choices=[app.name for app in apps.APPS],
                          default=apps.IDENTITY_DATABASE_APP.name)
    workload.add_argument('--rendered-manifest-dir', type=Path)
    workload.add_argument('--publish-address', default='127.0.0.1')
    workload.add_argument('--postgres-publish-address', default='')
    workload.add_argument('--service-port', type=int, default=8443)
    info = subcommands.add_parser('app-info')
    info.add_argument('--app', choices=[app.name for app in apps.APPS],
                      default=apps.IDENTITY_DATABASE_APP.name)
    subcommands.add_parser('replication-apps')
    replicate = subcommands.add_parser('replicate-workload')
    paths(replicate)
    replicate.add_argument('operation', choices=('primary', 'standby', 'status', 'authenticate'))
    replicate.add_argument('--app', choices=[app.name for app in apps.REPLICATED_APPS],
                           default=apps.IDENTITY_DATABASE_APP.name)
    replicate.add_argument('--node-address', default='')
    replicate.add_argument('--primary-address', default='')
    replicate.add_argument('--rendered-manifest-dir', type=Path)
    replicate.add_argument('--image-archive', type=Path)
    replicate.add_argument('--slot')
    remove = subcommands.add_parser('uninstall')
    remove.add_argument('--remove-data', action='store_true')
    remove.add_argument('--quadlet-dir', type=Path)
    down = subcommands.add_parser('down')
    down.add_argument('--rendered-manifest-dir', type=Path,
                      default=Path(__file__).resolve().parents[3] / 'generated/dev')
    args = parser.parse_args(argv)
    try:
        if args.command == 'app-info':
            print(json.dumps(apps.describe(next(app for app in apps.APPS if app.name == args.app))))
        elif args.command == 'replication-apps':
            print(json.dumps([app.name for app in apps.REPLICATED_APPS]))
        elif args.command == 'replicate-workload':
            from . import replication
            app = next(app for app in apps.REPLICATED_APPS if app.name == args.app)
            result = {'changed': False}
            if args.operation == 'primary':
                result['changed'] = replication.configure_primary(app, args.node_address)
            elif args.operation == 'standby':
                directory = args.quadlet_dir.resolve()
                result['changed'] = replication.bootstrap_standby(
                    app, args.primary_address, project_root=args.project_root,
                    quadlet_dir=directory,
                    kube_runtime_dir=(args.kube_runtime_dir or directory / 'todo-kube-runtime').resolve(),
                    rendered_manifest_dir=args.rendered_manifest_dir or args.project_root / 'generated/kube-runtime',
                    image_archive=args.image_archive, slot=args.slot)
            elif args.operation == 'authenticate':
                result['system_identifier'] = replication.authenticate(app, args.primary_address)
            else:
                result['status'] = replication.status(app)
            print(json.dumps(result))
        elif args.command == 'install':
            install.install(args.project_root, args.mode, args.deployment_mode, args.bundle_dir,
                            args.refresh_images, args.publish_address, args.service_port,
                            args.quadlet_dir, args.kube_runtime_dir)
        elif args.command == 'uninstall':
            uninstall.uninstall(args.remove_data, args.quadlet_dir)
            if not args.remove_data:
                volumes = ', '.join(app.volume('data') for app in apps.APPS)
                print(f'Database volumes {volumes} and database and Keycloak secrets were '
                      'preserved. Use --remove-data to delete them permanently.')
        elif args.command == 'down':
            kube_play.down(args.rendered_manifest_dir)
        else:
            directory = args.quadlet_dir.resolve()
            runtime = (args.kube_runtime_dir or directory / 'todo-kube-runtime').resolve()
            manifests = args.rendered_manifest_dir or args.project_root / 'generated/kube-runtime'
            kwargs = {'publish_address': args.publish_address, 'service_port': args.service_port}
            if args.workload == 'postgres':
                kwargs = {'publish_address': args.postgres_publish_address}
            if args.workload == 'keycloak':
                kwargs = {}
            selected_app = next(app for app in apps.APPS if app.name == args.app)
            if args.workload in ('postgres', 'application'):
                kwargs['app'] = selected_app
            elif selected_app != apps.IDENTITY_DATABASE_APP:
                raise ValueError('--app selects a postgres or application workload only.')
            function = {'postgres': workloads.install_postgres,
                        'application': workloads.install_application,
                        'keycloak': workloads.install_keycloak,
                        'shared-proxy': workloads.install_shared_proxy}[args.workload]
            changed = function(args.project_root, directory, runtime, manifests, **kwargs)
            # Existing Todo DR application calls also stage the shared identity workload.
            if args.workload == 'application' and selected_app == apps.IDENTITY_DATABASE_APP:
                changed = workloads.install_keycloak(
                    args.project_root, directory, runtime, manifests) or changed
            print(json.dumps({'changed': changed}))
    except (OSError, RuntimeError, ValueError, KeyError, TemplateError) as error:
        print(f'todo-installer: {error}', file=sys.stderr)
        return 1
    return 0
