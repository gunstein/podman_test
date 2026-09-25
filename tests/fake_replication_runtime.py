"""Inert command recorder for real Ansible/Python replication transport tests."""
import json
import os
import sys
from pathlib import Path

path = Path(os.environ['REPLICATION_TEST_STATE'])
state = json.loads(path.read_text()) if path.exists() else {
    'secrets': [], 'roles': [], 'hba': [], 'volumes': [], 'standbys': [], 'commands': []}
args = sys.argv[1:]
state['commands'].append([Path(sys.argv[0]).name, *args])


def finish(output='', code=0):
    path.write_text(json.dumps(state))
    if output:
        print(output)
    raise SystemExit(code)


if Path(sys.argv[0]).name == 'systemctl':
    if args == ['is-active', 'fapolicyd']:
        finish(code=3)
    if args[:2] in (['--user', 'daemon-reload'], ['--user', 'start']):
        finish()
    finish(code=99)
if args == ['kube', 'play', '--help']:
    finish('--no-pod-prefix')
if args[:2] == ['secret', 'exists']:
    # The standby fixture starts with transferred raw credentials.
    present = args[2] in state['secrets'] or (
        os.getenv('REPLICATION_TEST_STANDBY') == '1' and args[2].endswith('-password'))
    finish(code=0 if present else 1)
if args[:2] == ['secret', 'inspect']:
    finish('S' * 32)
if args[:2] == ['secret', 'create']:
    sys.stdin.read()
    state['secrets'].append(args[2])
    finish('fixture-secret-id')
if args[:2] == ['network', 'inspect']:
    finish(json.dumps([{'subnets': [{'subnet': '10.89.0.0/24'}]}]))
if args[:2] == ['image', 'exists']:
    finish()
if args[:2] == ['volume', 'exists']:
    finish(code=0 if args[2] in state['volumes'] else 1)
if args == ['kube', 'play', '-']:
    import yaml
    doc = yaml.safe_load(sys.stdin.read())
    assert doc['kind'] == 'PersistentVolumeClaim'
    state['volumes'].append(doc['metadata']['name'])
    finish()
if args and args[0] == 'exec':
    container = next(a for a in args if a.endswith('-postgres'))
    statement = sys.stdin.read()
    if 'pg_hba.conf' in statement:
        changed = container not in state['hba']
        state['hba'].append(container)
        finish('changed' if changed else '')
    if 'pg_is_in_recovery()' in statement:
        finish('t|on|0/20|0/20|0' if container in state['standbys'] else 'f|off|||0')
    if 'SELECT rolcanlogin' in statement:
        finish('t|t|f|f|f|f' if container in state['roles'] else '')
    if 'ALTER ROLE' in statement:
        state['roles'].append(container)
        finish()
    if 'pg_reload_conf' in statement:
        finish('t')
    if 'FROM pg_replication_slots' in statement:
        finish()
    finish(code=99)
if args and args[0] == 'run':
    if '--command=IDENTIFY_SYSTEM;' in args:
        finish('123|1|0/20|')
    if 'pg_basebackup' in args:
        volume = args[args.index('--volume') + 1].split(':')[0]
        state['standbys'].append(volume.removesuffix('-data'))
        finish()
    if '--entrypoint' in args and args[args.index('--entrypoint') + 1] in ('chmod', '/bin/sh'):
        finish()
    finish(code=99)
if args[:2] == ['wait', '--condition=healthy']:
    finish()
finish(code=99)
