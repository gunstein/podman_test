"""Command line interface; workload commands emit exactly one JSON result."""
import argparse
import json
import sys
from pathlib import Path

from jinja2 import TemplateError

from . import install, kube_play, uninstall, workloads


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
    workload.add_argument('workload', choices=('postgres', 'application', 'shared-proxy'))
    workload.add_argument('--rendered-manifest-dir', type=Path)
    workload.add_argument('--publish-address', default='127.0.0.1')
    workload.add_argument('--postgres-publish-address', default='')
    workload.add_argument('--service-port', type=int, default=8443)
    remove = subcommands.add_parser('uninstall')
    remove.add_argument('--remove-data', action='store_true')
    remove.add_argument('--quadlet-dir', type=Path)
    down = subcommands.add_parser('down')
    down.add_argument('--rendered-manifest-dir', type=Path,
                      default=Path(__file__).resolve().parents[3] / 'generated/dev')
    args = parser.parse_args(argv)
    try:
        if args.command == 'install':
            install.install(args.project_root, args.mode, args.deployment_mode, args.bundle_dir,
                            args.refresh_images, args.publish_address, args.service_port,
                            args.quadlet_dir, args.kube_runtime_dir)
        elif args.command == 'uninstall':
            uninstall.uninstall(args.remove_data, args.quadlet_dir)
            if not args.remove_data:
                print('Database volume todo-postgres-data and its database and Keycloak '
                      'secrets were preserved. Use --remove-data to delete them permanently.')
        elif args.command == 'down':
            kube_play.down(args.rendered_manifest_dir)
        else:
            directory = args.quadlet_dir.resolve()
            runtime = (args.kube_runtime_dir or directory / 'todo-kube-runtime').resolve()
            manifests = args.rendered_manifest_dir or args.project_root / 'generated/kube-runtime'
            kwargs = {'publish_address': args.publish_address, 'service_port': args.service_port}
            if args.workload == 'postgres':
                kwargs = {'publish_address': args.postgres_publish_address}
            function = {'postgres': workloads.install_postgres,
                        'application': workloads.install_application,
                        'shared-proxy': workloads.install_shared_proxy}[args.workload]
            changed = function(args.project_root, directory, runtime, manifests, **kwargs)
            print(json.dumps({'changed': changed}))
    except (OSError, RuntimeError, ValueError, KeyError, TemplateError) as error:
        print(f'todo-installer: {error}', file=sys.stderr)
        return 1
    return 0
