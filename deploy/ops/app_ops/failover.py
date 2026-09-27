"""One failover command for the surviving site, run after a person has decided the primary is lost.

It chains the existing steps and stops at the first failure, naming the step:

  1. promote   app_dr.py promote (its own preflight first), unless the
               promotion record already says the whole group was promoted
  2. deploy    the application tier (deploy-promoted-application)
  3. backup    WAL archiving and app_backup.py (configure-backup)
  4. ready     every service ready, Keycloak and both apps answer through
               nginx, and HTTPS on the host's address verifies with its CA
  5. users     what the operator must do so users reach this host

Running it again after a failure is safe: a completed promotion is skipped,
and the later steps change nothing that is already right. A failed or partial
promotion is never retried; a person must look at the record first.
"""
import json
import sys
from pathlib import Path

from . import recovery, steps
from .steps import apps, settings

APP_DR = '/opt/todo/bin/app_dr.py'


def promotion_state(host):
    """The state in the host's promotion record ('complete', 'failed', ...), or None if there is none."""
    journal = steps.paths(host)['config'] + '/promotion.json'
    text = host.run(['sh', '-c', 'test ! -e "$1" || cat "$1"', 'read-record', journal]).stdout
    return json.loads(text)['state'] if text.strip() else None


def promote(host, confirm_fenced, confirm_promotion):
    """Promote the database group once; True if it happened now, False if an earlier run did it."""
    state = promotion_state(host)
    if state == 'complete':
        return False
    if state is not None:
        raise RuntimeError(f'the promotion record says "{state}". Inspect every database role and '
                           f'{steps.paths(host)["config"]}/promotion.json; failover never retries a promotion.')
    host.run(['python3', APP_DR, 'promote', '--confirm-primary-fenced', confirm_fenced,
              '--confirm-promotion', confirm_promotion])
    return True


def ready(project_root, host):
    """Raise unless users can log in: services ready, Keycloak and apps through nginx, HTTPS verified."""
    wait_ready = (Path(project_root) / 'deploy/scripts/wait-ready.sh').read_text()
    waited = host.run(['bash', '-s', '--', 'app'], input=wait_ready, allowed=(0, 1))
    if waited.returncode:
        raise RuntimeError(waited.stdout.strip().splitlines()[-1] if waited.stdout.strip() else 'not ready')
    for app in apps.APPS:
        host.run(['curl', '--silent', '--show-error', '--fail', '--max-time', '10', '-H', f'Host: {app.hostname}',
                  f'http://127.0.0.1:{settings.LOCAL_HTTP_PORT}/auth/realms/todo/.well-known/openid-configuration'])
        host.run(['bash', '-c', 'curl --silent --show-error --fail --max-time 10 '
                  '--cacert <(podman exec nginx cat /var/lib/todo-tls/ca.crt) '
                  '--resolve "$1:$2:$3" "https://$1:$2/ready"',
                  'check-https', app.hostname, str(settings.HTTPS_PORT), host.spec.address])


def users(host):
    """What the operator must do so users reach this host: the names, the address and the CA to trust."""
    fingerprint = host.run(['podman', 'exec', 'nginx', 'openssl', 'x509', '-in', '/var/lib/todo-tls/ca.crt',
                            '-noout', '-fingerprint', '-sha256']).stdout.strip().split('=', 1)[-1]
    names = [app.hostname for app in apps.APPS]
    return {'hostnames': names, 'address': host.spec.address, 'ca_sha256': fingerprint,
            'next': f'Point {" and ".join(names)} at {host.spec.address} (DNS or each client\'s hosts file), '
                    f'and have clients trust this host\'s CA, SHA-256 {fingerprint}.'}


def failover(project_root, controller, current, old_primary, confirm_fenced, confirm_promotion, say=None):
    """Run the steps in order on the promoted host itself; return a report with 'changed' and 'users'."""
    say = say or (lambda message: print(f'app-ops failover: {message}', file=sys.stderr, flush=True))
    if not current.spec.local:
        raise RuntimeError('failover runs on the surviving host itself: mark it local: true')
    if confirm_fenced != f'{old_primary.name} is fenced' or confirm_promotion != current.name:
        raise RuntimeError(f'confirmations must be exactly --confirm-primary-fenced "{old_primary.name} is fenced" '
                           f'--confirm-promotion {current.name}')
    recovery.require_identity(current)
    report = {}
    for name, action in (
            ('promote', lambda: promote(current, confirm_fenced, confirm_promotion)),
            ('deploy', lambda: recovery.deploy_promoted(project_root, controller, current)),
            ('backup', lambda: recovery.configure_backup(project_root, controller, current)),
            ('ready', lambda: ready(project_root, current)),
            ('users', lambda: users(current))):
        say(f'{name} ...')
        try:
            report[name] = action()
        except RuntimeError as error:
            raise RuntimeError(f'failover stopped at step "{name}": {error} -- the steps before it are done; '
                               'fix the cause, then run failover again') from error
        say(f'{name} done')
    return {'changed': any(report[name] for name in ('promote', 'deploy', 'backup')),
            'promoted_now': report['promote'], 'users': report['users']}
