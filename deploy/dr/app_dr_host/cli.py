"""Command line interface for DR on each host; every command prints one JSON result.

These are the building blocks app_ops runs on each host over SSH: replicate
a database, publish the primaries, reseed the old primary or a standby, deploy the
application on a promoted host, nginx's certificate from the organisation's CA
(nginx-tls), and the status and pair checks. The single
host installer (app_installer) knows nothing about them. Errors print one
"app-dr-host: ..." line on stderr and return 1.

--project-root, before the command, is where app-ops put the operations
package's files on this host (app_ops.steps.stage_target_files); every
command takes the platform from its bundle.json, never from the code.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

from app_installer import oplog, settings, target_render

from . import nginx_tls, pair, promoted, replication, transfer


def paths(parser):
    """Add the options every installing command shares: Quadlet and Kube runtime directories."""
    parser.add_argument('--quadlet-dir', type=Path, default=settings.QUADLET_DIR)
    parser.add_argument('--kube-runtime-dir', type=Path)


def target(args, node_address):
    """The bundle's files filled in for this host: its address, and hostnames given (--target-values) or recorded."""
    return target_render.load_on_host(args.project_root, node_address, args.target_values)


def main(argv=None):
    """Parse one subcommand, run it, and return the exit code."""
    parser = argparse.ArgumentParser(description='DR building blocks on one host')
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[3],
                        help="the operations package's files on this host, bundle.json among them")
    subcommands = parser.add_subparsers(dest='command', required=True)
    replicate = subcommands.add_parser('replicate-workload')
    paths(replicate)
    replicate.add_argument('operation', choices=('primary', 'standby', 'status', 'authenticate', 'streaming', 'hba',
                                                   'rebuild-primary-check', 'quarantined', 'reseed-check',
                                                   'replication-path', 'slot', 'drop-slot'))
    replicate.add_argument('--app', required=True, help="a database of the platform's DR group")
    replicate.add_argument('--node-address', default='')
    replicate.add_argument('--primary-address', default='')
    replicate.add_argument('--image-archive', type=Path)
    replicate.add_argument('--slot')
    replicate.add_argument('--rebuilt', action='store_true')
    replicate.add_argument('--confirm-fenced', default='')
    replicate.add_argument('--confirm-reseed', default='')
    replicate.add_argument('--target-values', type=json.loads, default=None,
                           help="the primary's hostnames (target-values on the primary)")
    reseed_check = subcommands.add_parser('standby-reseed-check')
    reseed_check.add_argument('--primary-address', required=True)
    erase = subcommands.add_parser('erase-standby')
    erase.add_argument('--primary-address', required=True)
    erase.add_argument('--confirm-reseed', required=True)
    cluster = subcommands.add_parser('cluster-status')
    cluster.add_argument('role', choices=('primary', 'standby'))
    reseed = subcommands.add_parser('reseed-group')
    paths(reseed)
    reseed.add_argument('--primary-address', required=True)
    reseed.add_argument('--confirm-fenced', required=True)
    reseed.add_argument('--confirm-reseed', required=True)
    reseed.add_argument('--node-address', required=True)
    reseed.add_argument('--target-values', type=json.loads, default=None,
                        help="the primary's hostnames (target-values on the primary)")
    publish = subcommands.add_parser('publish-primaries')
    paths(publish)
    publish.add_argument('mode', choices=('bootstrap', 'redundancy'))
    publish.add_argument('--node-address', required=True)
    promoted_group = subcommands.add_parser('require-promoted-group')
    promoted_group.add_argument('--journal', type=Path,
                          default=Path.home() / settings.DR_CONFIG / settings.PROMOTION_RECORD)
    promoted_tier = subcommands.add_parser('deploy-promoted')
    paths(promoted_tier)
    promoted_tier.add_argument('--bundle-dir', type=Path, required=True)
    promoted_tier.add_argument('--inventory-hostname', required=True)
    promoted_tier.add_argument('--node-address', required=True)
    promoted_tier.add_argument('--service-port', type=int, default=settings.HTTPS_PORT)
    promoted_tier.add_argument('--journal', type=Path, default=Path.home() / settings.DR_CONFIG / settings.PROMOTION_RECORD)
    promoted_tier.add_argument('--config-dir', type=Path, default=Path.home() / '.config/platform')
    node = subcommands.add_parser('node-facts')
    node.add_argument('--inventory-hostname', required=True)
    node.add_argument('--role', required=True)
    node.add_argument('--address', required=True)
    pair_check = subcommands.add_parser('check-standby-pair')
    pair_check.add_argument('primary', type=json.loads, help='node-facts output from primary')
    pair_check.add_argument('standby', type=json.loads, help='node-facts output from standby')
    subcommands.add_parser('target-values')
    subcommands.add_parser('export-replication-secrets')
    subcommands.add_parser('import-replication-secrets')
    certificate = subcommands.add_parser('nginx-tls')
    certificate.add_argument('operation', choices=('request', 'install', 'mode'))
    certificate.add_argument('--bundle-dir', type=Path)
    certificate.add_argument('--node-address')
    certificate.add_argument('--certificate', type=Path, help='install: the signed certificate on this host')
    certificate.add_argument('--ca', type=Path, help="install: the organisation's root CA on this host")
    certificate.add_argument('--mode', choices=('local', 'provided'), help="mode: the pair's nginx TLS mode")
    args = parser.parse_args(argv)
    oplog.describe(args.command, getattr(args, 'operation', None), getattr(args, 'app', None))
    try:
        platform = target_render.bundle_platform(args.project_root)
        if args.command == 'replicate-workload':
            database = next((d for d in platform.replicated_databases if d.name == args.app), None)
            if database is None:
                raise ValueError(f'--app {args.app}: not a database of the DR group, which is '
                                 f'{", ".join(d.name for d in platform.replicated_databases)}')
            result: dict = {'changed': False}
            if args.operation == 'primary':
                result['changed'] = replication.configure_primary(database, args.node_address)
            elif args.operation in ('standby', 'reseed-check'):
                directory = args.quadlet_dir.resolve()
                files = target(args, args.node_address)
                options = dict(project_root=args.project_root, quadlet_dir=directory,
                               kube_runtime_dir=(args.kube_runtime_dir or directory / settings.KUBE_RUNTIME).resolve(),
                               target=files)
                if args.operation == 'standby':
                    target_render.record_platform(platform)
                    result['changed'] = replication.bootstrap_standby(
                        database, args.primary_address, image_archive=args.image_archive, slot=args.slot, **options)
                    target_render.write_record(files.values)
                else:
                    if args.slot is not None or args.image_archive is not None:
                        raise ValueError('Reseed uses the registered rebuild slot and requires the existing image')
                    result['changed'] = replication.reseed_check(
                        database, args.primary_address, confirm_fenced=args.confirm_fenced,
                        confirm_reseed=args.confirm_reseed, **options)
            elif args.operation == 'rebuild-primary-check':
                result['changed'] = replication.rebuild_primary_check(database)
            elif args.operation == 'quarantined':
                result['changed'] = replication.require_quarantined_group(platform)
            elif args.operation == 'hba':
                replication.require_primary(database)
                result['changed'] = replication.refresh_hba(database)
            elif args.operation == 'streaming':
                result['status'] = replication.streaming_status(database, rebuilt=args.rebuilt, slot=args.slot)
            elif args.operation == 'slot':
                result['slot'] = replication.standby_slot(database)
            elif args.operation == 'drop-slot':
                if not args.slot:
                    raise ValueError('drop-slot needs --slot')
                result['changed'] = replication.drop_idle_slot(database, args.slot)
            elif args.operation == 'replication-path':
                result['path'] = replication.replication_path(database, args.primary_address)
            elif args.operation == 'authenticate':
                result['system_identifier'] = replication.authenticate(database, args.primary_address)
            else:
                result['status'] = replication.status(database)
            print(json.dumps(result))
        elif args.command == 'standby-reseed-check':
            print(json.dumps({'changed': replication.standby_reseed_check(platform, args.primary_address)}))
        elif args.command == 'erase-standby':
            erased = replication.erase_standby_group(platform, args.primary_address, args.confirm_reseed)
            print(json.dumps({'changed': True, 'erased': erased}))
        elif args.command == 'cluster-status':
            print(json.dumps({'changed': False, 'status': replication.cluster_status(platform, args.role)}))
        elif args.command == 'reseed-group':
            directory = args.quadlet_dir.resolve()
            files = target(args, args.node_address)
            target_render.record_platform(platform)
            reseeded = replication.reseed_group(
                args.primary_address, confirm_fenced=args.confirm_fenced, confirm_reseed=args.confirm_reseed,
                project_root=args.project_root, quadlet_dir=directory,
                kube_runtime_dir=(args.kube_runtime_dir or directory / settings.KUBE_RUNTIME).resolve(), target=files)
            target_render.write_record(files.values)
            print(json.dumps({'changed': True, 'reseeded': reseeded}))
        elif args.command == 'publish-primaries':
            directory = args.quadlet_dir.resolve()
            args.target_values = None  # a primary keeps the hostnames it was installed with
            files = target(args, args.node_address)
            target_render.record_platform(platform)
            result = replication.publish_primaries(
                args.node_address, bootstrap=args.mode == 'bootstrap', project_root=args.project_root,
                quadlet_dir=directory,
                kube_runtime_dir=(args.kube_runtime_dir or directory / settings.KUBE_RUNTIME).resolve(), target=files)
            target_render.write_record(files.values)
            print(json.dumps(result))
        elif args.command == 'target-values':
            print(json.dumps({'changed': False, 'values': target_render.host_hostnames(args.project_root)}))
        elif args.command == 'require-promoted-group':
            print(json.dumps({'changed': replication.require_promoted_group(platform, args.journal)}))
        elif args.command == 'deploy-promoted':
            print(json.dumps({'changed': promoted.deploy(
                project_root=args.project_root, quadlet_dir=args.quadlet_dir.resolve(),
                bundle_dir=args.bundle_dir, inventory_hostname=args.inventory_hostname,
                node_address=args.node_address, journal=args.journal, config_dir=args.config_dir,
                service_port=args.service_port)}))
        elif args.command == 'node-facts':
            print(json.dumps(pair.node_facts(platform, args.inventory_hostname, args.role, args.address)))
        elif args.command == 'check-standby-pair':
            pair.check_pair(platform, args.primary, args.standby)
            print(json.dumps({'changed': False}))
        elif args.command == 'nginx-tls':
            if args.operation == 'mode':
                if not args.mode:
                    raise ValueError('nginx-tls mode needs --mode')
                print(json.dumps({'changed': nginx_tls.set_mode(args.mode)}))
            else:
                if not (args.bundle_dir and args.node_address):
                    raise ValueError(f'nginx-tls {args.operation} needs --bundle-dir and --node-address')
                where = (args.project_root, args.bundle_dir, args.node_address)
                if args.operation == 'request':
                    csr, names = nginx_tls.request(*where)
                    print(json.dumps({'changed': True, 'request': csr, 'hostnames': names}))
                else:
                    if not (args.certificate and args.ca):
                        raise ValueError('nginx-tls install needs --certificate and --ca')
                    print(json.dumps({'changed': nginx_tls.install(*where, args.certificate, args.ca)}))
        elif args.command == 'export-replication-secrets':
            # Value-bearing stdout: callers must pipe it, never log or store it. Base64
            # keeps it opaque, so no caller parses or reformats the values on the way.
            print(base64.b64encode(json.dumps(transfer.export_replicated(platform)).encode()).decode())
        elif args.command == 'import-replication-secrets':
            values = json.loads(base64.b64decode(sys.stdin.read().strip(), validate=True))
            print(json.dumps({'changed': transfer.import_replicated(platform, values)}))
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f'app-dr-host: {error}', file=sys.stderr)
        return 1
    return 0
