"""app-ops: DR operations over plain SSH, one command per operation (see deploy/dr/README.md)."""
import argparse
import json
import sys
from pathlib import Path

from app_installer import oplog

from . import failover, inventory, nginx_tls, quarantine, recovery, standby
from .transport import Host

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL = inventory.HostSpec(name='controller', role='controller', address='127.0.0.1', user='', home='', local=True)
INITIAL = ('primary', 'standby')
RECOVERY = ('current_primary', 'rebuild_standby')
COMMANDS = {
    'install-quarantine-tool': INITIAL, 'preflight-standby': INITIAL, 'sync-standby-secrets': INITIAL,
    'bootstrap-standby': INITIAL, 'replication-status': INITIAL, 'install-dr-tool': INITIAL,
    'reseed-standby': INITIAL, 'nginx-tls-request': INITIAL, 'nginx-tls-install': INITIAL,
    'failover': RECOVERY, 'deploy-promoted-application': RECOVERY, 'configure-backup': RECOVERY, 'preflight-standby-rebuild': RECOVERY,
    'rebuild-standby': RECOVERY, 'cluster-status': RECOVERY,
}


def parser():
    """Command-line arguments: --inventory, then one command and its options."""
    result = argparse.ArgumentParser(prog='app-ops', description=__doc__)
    result.add_argument('--inventory', type=Path, required=True)
    commands = result.add_subparsers(dest='command', required=True)
    for name in COMMANDS:
        command = commands.add_parser(name)
        if name == 'install-quarantine-tool':
            command.add_argument('--enable-guest-exec', action='store_true')
            command.add_argument('--enable-selinux-entrypoint', action='store_true')
        if name == 'failover':
            command.add_argument('--confirm-primary-fenced', required=True, help='exactly "<old primary> is fenced"')
            command.add_argument('--confirm-promotion', required=True, help='exactly "<this host>"')
        if name == 'reseed-standby':
            command.add_argument('--confirm-reseed', required=True, help='exactly "<standby host>"')
        if name == 'nginx-tls-request':
            command.add_argument('--output', type=Path, required=True, help='where <host>.csr is written')
        if name == 'nginx-tls-install':
            command.add_argument('--certificates', type=Path, required=True,
                                 help='a directory holding <host>.crt for both hosts')
            command.add_argument('--ca', type=Path, required=True, help="your CA's root certificate (PEM)")
        if name in ('preflight-standby-rebuild', 'rebuild-standby'):
            command.add_argument('--confirm-fenced', required=True, help='exactly "<rebuild host> is fenced"')
            command.add_argument('--confirm-reseed', required=True, help='exactly "<rebuild host>"')
    return result


def dispatch(args, controller, hosts):
    """Run the chosen command with its hosts; return its result (a changed flag or a report)."""
    root = PROJECT_ROOT
    if args.command == 'install-quarantine-tool':
        return quarantine.install(root, controller, hosts['primary'], guest_exec=args.enable_guest_exec,
                                  selinux_entrypoint=args.enable_selinux_entrypoint)
    if args.command in ('preflight-standby', 'sync-standby-secrets', 'bootstrap-standby', 'replication-status'):
        function = {'preflight-standby': standby.preflight, 'sync-standby-secrets': standby.sync_secrets,
                    'bootstrap-standby': standby.bootstrap, 'replication-status': standby.replication_status}
        return function[args.command](root, controller, hosts['primary'], hosts['standby'])
    if args.command == 'install-dr-tool':
        return standby.install_dr_tools(root, controller, hosts['primary'], hosts['standby'])
    if args.command == 'nginx-tls-request':
        return nginx_tls.request(root, controller, hosts['primary'], hosts['standby'], args.output)
    if args.command == 'nginx-tls-install':
        return nginx_tls.install(root, controller, hosts['primary'], hosts['standby'], args.certificates, args.ca)
    if args.command == 'reseed-standby':
        return recovery.reseed_standby(root, controller, hosts['primary'], hosts['standby'], args.confirm_reseed)
    current, rebuild = hosts['current_primary'], hosts['rebuild_standby']
    if args.command == 'failover':
        return failover.failover(root, controller, current, rebuild, args.confirm_primary_fenced,
                                 args.confirm_promotion)
    if args.command == 'deploy-promoted-application':
        return recovery.deploy_promoted(root, controller, current)
    if args.command == 'configure-backup':
        return recovery.configure_backup(root, controller, current)
    if args.command == 'preflight-standby-rebuild':
        recovery.preflight_rebuild(root, controller, current, rebuild, args.confirm_fenced, args.confirm_reseed)
        return False
    if args.command == 'rebuild-standby':
        return recovery.rebuild(root, controller, current, rebuild, args.confirm_fenced, args.confirm_reseed)
    return recovery.cluster_status(current, rebuild)


def main(argv=None):
    """Load the inventory for the command's roles, run it, and print one JSON result.

    Errors print one "app-ops: ..." line on stderr and return 1.
    """
    args = parser().parse_args(argv)
    oplog.describe(args.command)
    try:
        specs = inventory.load(args.inventory, COMMANDS[args.command])
        result = dispatch(args, Host(LOCAL), {role: Host(spec) for role, spec in specs.items()})
        print(json.dumps(result if isinstance(result, dict) else {'changed': result}))
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f'app-ops: {error}', file=sys.stderr)
        return 1
