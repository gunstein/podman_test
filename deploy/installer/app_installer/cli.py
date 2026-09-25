"""Command line interface; workload commands emit exactly one JSON result."""
import argparse
import base64
import json
import sys
from pathlib import Path

from jinja2 import TemplateError

from . import apps, install, kube_play, settings, uninstall, workloads


def paths(parser):
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--quadlet-dir', type=Path, default=settings.QUADLET_DIR)
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
    deploy.add_argument('--service-port', type=int, default=settings.HTTPS_PORT)
    workload = subcommands.add_parser('install-workload')
    paths(workload)
    workload.add_argument('workload', choices=('postgres', 'application', 'keycloak', 'shared-proxy'))
    workload.add_argument('--app', choices=[app.name for app in apps.APPS] + [apps.KEYCLOAK_DATABASE.name],
                          default=apps.SHARED_RESOURCE_OWNER.name)
    workload.add_argument('--rendered-manifest-dir', type=Path)
    workload.add_argument('--publish-address', default='127.0.0.1')
    workload.add_argument('--postgres-publish-address', default='')
    workload.add_argument('--service-port', type=int, default=settings.HTTPS_PORT)
    info = subcommands.add_parser('app-info')
    info.add_argument('--app', choices=[app.name for app in apps.APPS] + [apps.KEYCLOAK_DATABASE.name],
                      default=apps.SHARED_RESOURCE_OWNER.name)
    registry = subcommands.add_parser('replication-apps')
    registry.add_argument('--details', action='store_true')
    replicate = subcommands.add_parser('replicate-workload')
    paths(replicate)
    replicate.add_argument('operation', choices=('primary', 'standby', 'status', 'authenticate', 'streaming', 'hba',
                                                   'rebuild-primary-check', 'quarantined', 'reseed-check', 'reseed'))
    replicate.add_argument('--app', choices=[d.name for d in apps.REPLICATED_DATABASES],
                           default=apps.SHARED_RESOURCE_OWNER.name)
    replicate.add_argument('--node-address', default='')
    replicate.add_argument('--primary-address', default='')
    replicate.add_argument('--rendered-manifest-dir', type=Path)
    replicate.add_argument('--image-archive', type=Path)
    replicate.add_argument('--slot')
    replicate.add_argument('--rebuilt', action='store_true')
    replicate.add_argument('--confirm-fenced', default='')
    replicate.add_argument('--confirm-reseed', default='')
    promoted = subcommands.add_parser('require-promoted-group')
    promoted.add_argument('--journal', type=Path,
                          default=Path.home() / '.config/todo/promotion.json')
    recovered_images = subcommands.add_parser('prepare-promoted-images')
    recovered_images.add_argument('--bundle-dir', type=Path, required=True)
    subcommands.add_parser('configure-clients')
    subcommands.add_parser('export-replication-secrets')
    subcommands.add_parser('import-replication-secrets')
    service_list = subcommands.add_parser('services')
    service_list.add_argument('--application-tier', action='store_true')
    remove = subcommands.add_parser('uninstall')
    remove.add_argument('--remove-data', action='store_true')
    remove.add_argument('--quadlet-dir', type=Path)
    down = subcommands.add_parser('down')
    down.add_argument('--rendered-manifest-dir', type=Path,
                      default=Path(__file__).resolve().parents[3] / 'generated/dev')
    args = parser.parse_args(argv)
    try:
        if args.command == 'app-info':
            selected = (apps.KEYCLOAK_DATABASE if args.app == apps.KEYCLOAK_DATABASE.name
                       else next(app for app in apps.APPS if app.name == args.app))
            print(json.dumps(apps.describe(selected)))
        elif args.command == 'replication-apps':
            print(json.dumps([apps.describe(d) if args.details else d.name
                              for d in apps.REPLICATED_DATABASES]))
        elif args.command == 'replicate-workload':
            from . import replication
            app = next(d for d in apps.REPLICATED_DATABASES if d.name == args.app)
            result = {'changed': False}
            if args.operation == 'primary':
                result['changed'] = replication.configure_primary(app, args.node_address)
            elif args.operation in ('standby', 'reseed-check', 'reseed'):
                directory = args.quadlet_dir.resolve()
                options = dict(project_root=args.project_root, quadlet_dir=directory,
                               kube_runtime_dir=(args.kube_runtime_dir or directory / 'todo-kube-runtime').resolve(),
                               rendered_manifest_dir=args.rendered_manifest_dir or args.project_root / 'generated/kube-runtime')
                if args.operation == 'standby':
                    result['changed'] = replication.bootstrap_standby(
                        app, args.primary_address, image_archive=args.image_archive, slot=args.slot, **options)
                else:
                    if args.slot is not None or args.image_archive is not None:
                        raise ValueError('Reseed uses the registered rebuild slot and requires the existing image')
                    function = replication.reseed_check if args.operation == 'reseed-check' else replication.reseed_standby
                    result['changed'] = function(app, args.primary_address, confirm_fenced=args.confirm_fenced,
                                                 confirm_reseed=args.confirm_reseed, **options)
            elif args.operation == 'rebuild-primary-check':
                result['changed'] = replication.rebuild_primary_check(app)
            elif args.operation == 'quarantined':
                result['changed'] = replication.require_quarantined_group()
            elif args.operation == 'hba':
                replication.require_primary(app)
                result['changed'] = replication.refresh_hba(app)
            elif args.operation == 'streaming':
                result['status'] = replication.streaming_status(app, rebuilt=args.rebuilt)
            elif args.operation == 'authenticate':
                result['system_identifier'] = replication.authenticate(app, args.primary_address)
            else:
                result['status'] = replication.status(app)
            print(json.dumps(result))
        elif args.command == 'require-promoted-group':
            from . import replication
            print(json.dumps({'changed': replication.require_promoted_group(args.journal)}))
        elif args.command == 'prepare-promoted-images':
            from . import images
            changed = any(images.prepare_shared(args.bundle_dir, 'offline', args.bundle_dir).values())
            for app in apps.APPS:
                changed = any(images.prepare(args.bundle_dir, 'offline', args.bundle_dir,
                                             app=app, include_shared=False).values()) or changed
            print(json.dumps({'changed': changed}))
        elif args.command == 'configure-clients':
            from . import keycloak, secrets
            changed = keycloak.configure(secrets.read(apps.KEYCLOAK_ADMIN_SECRET),
                                         [(app.keycloak_client, app.hostname) for app in apps.APPS])
            print(json.dumps({'changed': changed}))
        elif args.command == 'export-replication-secrets':
            from . import secrets
            # Value-bearing stdout: callers must pipe it, never log or store it. Base64
            # keeps it opaque; Ansible templating would otherwise parse JSON into a dict.
            print(base64.b64encode(json.dumps(secrets.export_replicated()).encode()).decode())
        elif args.command == 'import-replication-secrets':
            from . import secrets
            transfer = json.loads(base64.b64decode(sys.stdin.read().strip(), validate=True))
            print(json.dumps({'changed': secrets.import_replicated(transfer)}))
        elif args.command == 'services':
            print(json.dumps(apps.services(databases=not args.application_tier)))
        elif args.command == 'install':
            changed = install.install(args.project_root, args.mode, args.deployment_mode, args.bundle_dir,
                                      args.refresh_images, args.publish_address, args.service_port,
                                      args.quadlet_dir, args.kube_runtime_dir)
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
        elif args.command == 'down':
            if not kube_play.down(args.rendered_manifest_dir):
                print('No installed development manifests were found under '
                      f'{args.rendered_manifest_dir}; nothing was torn down.', file=sys.stderr)
        else:
            directory = args.quadlet_dir.resolve()
            install.preflight(directory)
            runtime = (args.kube_runtime_dir or directory / 'todo-kube-runtime').resolve()
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
            if args.workload in ('postgres', 'application'):
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
    except (OSError, RuntimeError, ValueError, KeyError, TemplateError) as error:
        print(f'app-installer: {error}', file=sys.stderr)
        return 1
    return 0
