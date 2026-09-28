"""Command line interface for DR on each host; every command prints one JSON result.

These are the building blocks app_ops runs on each host over SSH: replicate
a database, publish the primaries, reseed the old primary, deploy the
application on a promoted host, and the status and pair checks. The single
host installer (app_installer) knows nothing about them. Errors print one
"app-dr-host: ..." line on stderr and return 1.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

from app_installer import apps, settings
from jinja2 import TemplateError

from . import pair, promoted, replication, transfer


def paths(parser):
    """Add the options every installing command shares: project root, Quadlet and Kube runtime directories."""
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--quadlet-dir', type=Path, default=settings.QUADLET_DIR)
    parser.add_argument('--kube-runtime-dir', type=Path)


def main(argv=None):
    """Parse one subcommand, run it, and return the exit code."""
    parser = argparse.ArgumentParser(description='DR building blocks on one host')
    subcommands = parser.add_subparsers(dest='command', required=True)
    replicate = subcommands.add_parser('replicate-workload')
    paths(replicate)
    replicate.add_argument('operation', choices=('primary', 'standby', 'status', 'authenticate', 'streaming', 'hba',
                                                   'rebuild-primary-check', 'quarantined', 'reseed-check',
                                                   'replication-path'))
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
    cluster = subcommands.add_parser('cluster-status')
    cluster.add_argument('role', choices=('primary', 'standby'))
    reseed = subcommands.add_parser('reseed-group')
    paths(reseed)
    reseed.add_argument('--primary-address', required=True)
    reseed.add_argument('--rendered-manifest-dir', type=Path, required=True)
    reseed.add_argument('--confirm-fenced', required=True)
    reseed.add_argument('--confirm-reseed', required=True)
    publish = subcommands.add_parser('publish-primaries')
    paths(publish)
    publish.add_argument('mode', choices=('bootstrap', 'redundancy'))
    publish.add_argument('--node-address', required=True)
    publish.add_argument('--rendered-manifest-dir', type=Path, required=True)
    promoted_group = subcommands.add_parser('require-promoted-group')
    promoted_group.add_argument('--journal', type=Path,
                          default=Path.home() / '.config/todo/promotion.json')
    promoted_tier = subcommands.add_parser('deploy-promoted')
    paths(promoted_tier)
    promoted_tier.add_argument('--bundle-dir', type=Path, required=True)
    promoted_tier.add_argument('--inventory-hostname', required=True)
    promoted_tier.add_argument('--node-address', required=True)
    promoted_tier.add_argument('--service-port', type=int, default=settings.HTTPS_PORT)
    promoted_tier.add_argument('--journal', type=Path, default=Path.home() / '.config/todo/promotion.json')
    promoted_tier.add_argument('--config-dir', type=Path, default=Path.home() / '.config/todo')
    node = subcommands.add_parser('node-facts')
    node.add_argument('--inventory-hostname', required=True)
    node.add_argument('--role', required=True)
    node.add_argument('--address', required=True)
    pair_check = subcommands.add_parser('check-standby-pair')
    pair_check.add_argument('primary', type=json.loads, help='node-facts output from primary')
    pair_check.add_argument('standby', type=json.loads, help='node-facts output from standby')
    subcommands.add_parser('export-replication-secrets')
    subcommands.add_parser('import-replication-secrets')
    args = parser.parse_args(argv)
    try:
        if args.command == 'replicate-workload':
            database = next(d for d in apps.REPLICATED_DATABASES if d.name == args.app)
            result = {'changed': False}
            if args.operation == 'primary':
                result['changed'] = replication.configure_primary(database, args.node_address)
            elif args.operation in ('standby', 'reseed-check'):
                directory = args.quadlet_dir.resolve()
                options = dict(project_root=args.project_root, quadlet_dir=directory,
                               kube_runtime_dir=(args.kube_runtime_dir or directory / 'todo-kube-runtime').resolve(),
                               rendered_manifest_dir=args.rendered_manifest_dir or args.project_root / 'generated/kube-runtime')
                if args.operation == 'standby':
                    result['changed'] = replication.bootstrap_standby(
                        database, args.primary_address, image_archive=args.image_archive, slot=args.slot, **options)
                else:
                    if args.slot is not None or args.image_archive is not None:
                        raise ValueError('Reseed uses the registered rebuild slot and requires the existing image')
                    result['changed'] = replication.reseed_check(
                        database, args.primary_address, confirm_fenced=args.confirm_fenced,
                        confirm_reseed=args.confirm_reseed, **options)
            elif args.operation == 'rebuild-primary-check':
                result['changed'] = replication.rebuild_primary_check(database)
            elif args.operation == 'quarantined':
                result['changed'] = replication.require_quarantined_group()
            elif args.operation == 'hba':
                replication.require_primary(database)
                result['changed'] = replication.refresh_hba(database)
            elif args.operation == 'streaming':
                result['status'] = replication.streaming_status(database, rebuilt=args.rebuilt)
            elif args.operation == 'replication-path':
                result['path'] = replication.replication_path(database, args.primary_address)
            elif args.operation == 'authenticate':
                result['system_identifier'] = replication.authenticate(database, args.primary_address)
            else:
                result['status'] = replication.status(database)
            print(json.dumps(result))
        elif args.command == 'cluster-status':
            print(json.dumps({'changed': False, 'status': replication.cluster_status(args.role)}))
        elif args.command == 'reseed-group':
            directory = args.quadlet_dir.resolve()
            print(json.dumps({'changed': True, 'reseeded': replication.reseed_group(
                args.primary_address, confirm_fenced=args.confirm_fenced, confirm_reseed=args.confirm_reseed,
                project_root=args.project_root, quadlet_dir=directory,
                kube_runtime_dir=(args.kube_runtime_dir or directory / 'todo-kube-runtime').resolve(),
                rendered_manifest_dir=args.rendered_manifest_dir)}))
        elif args.command == 'publish-primaries':
            directory = args.quadlet_dir.resolve()
            print(json.dumps(replication.publish_primaries(
                args.node_address, bootstrap=args.mode == 'bootstrap', project_root=args.project_root,
                quadlet_dir=directory,
                kube_runtime_dir=(args.kube_runtime_dir or directory / 'todo-kube-runtime').resolve(),
                rendered_manifest_dir=args.rendered_manifest_dir)))
        elif args.command == 'require-promoted-group':
            print(json.dumps({'changed': replication.require_promoted_group(args.journal)}))
        elif args.command == 'deploy-promoted':
            print(json.dumps({'changed': promoted.deploy(
                project_root=args.project_root, quadlet_dir=args.quadlet_dir.resolve(),
                bundle_dir=args.bundle_dir, inventory_hostname=args.inventory_hostname,
                node_address=args.node_address, journal=args.journal, config_dir=args.config_dir,
                service_port=args.service_port)}))
        elif args.command == 'node-facts':
            print(json.dumps(pair.node_facts(args.inventory_hostname, args.role, args.address)))
        elif args.command == 'check-standby-pair':
            pair.check_pair(args.primary, args.standby)
            print(json.dumps({'changed': False}))
        elif args.command == 'export-replication-secrets':
            # Value-bearing stdout: callers must pipe it, never log or store it. Base64
            # keeps it opaque, so no caller parses or reformats the values on the way.
            print(base64.b64encode(json.dumps(transfer.export_replicated()).encode()).decode())
        elif args.command == 'import-replication-secrets':
            values = json.loads(base64.b64decode(sys.stdin.read().strip(), validate=True))
            print(json.dumps({'changed': transfer.import_replicated(values)}))
    except (OSError, RuntimeError, ValueError, KeyError, TemplateError) as error:
        print(f'app-dr-host: {error}', file=sys.stderr)
        return 1
    return 0
