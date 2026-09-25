"""todo-ops: DR operations over plain SSH, replacing the Ansible playbooks one at a time."""
import argparse
import json
import sys
from pathlib import Path

from . import inventory, quarantine
from .transport import Host

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL = inventory.HostSpec(name='controller', role='controller', address='127.0.0.1', user='', home='', local=True)


def parser():
    result = argparse.ArgumentParser(prog='todo-ops', description=__doc__)
    result.add_argument('--inventory', type=Path, required=True)
    commands = result.add_subparsers(dest='command', required=True)
    helper = commands.add_parser('install-quarantine-tool')
    helper.add_argument('--enable-guest-exec', action='store_true')
    helper.add_argument('--enable-selinux-entrypoint', action='store_true')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        controller = Host(LOCAL)
        if args.command == 'install-quarantine-tool':
            hosts = inventory.load(args.inventory, ('primary', 'standby'))
            changed = quarantine.install(PROJECT_ROOT, controller, Host(hosts['primary']),
                                         guest_exec=args.enable_guest_exec,
                                         selinux_entrypoint=args.enable_selinux_entrypoint)
        print(json.dumps({'changed': changed}))
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f'todo-ops: {error}', file=sys.stderr)
        return 1
