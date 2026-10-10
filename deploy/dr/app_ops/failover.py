"""One failover command for the surviving site, run after a person has decided the primary is lost.

It chains the existing steps and stops at the first failure, naming the step:

  1. promote     app_dr.py promote (its own preflight first), unless the
                 promotion record already says the whole group was promoted
  2. deploy      the application tier (deploy-promoted-application)
  3. backup      WAL archiving, app_backup.py and its nightly timer
                 (configure-backup)
  4. services    every service ready, and each app answers over HTTPS on the
                 host's address, verified with its CA
  5. login-page  for each app, Keycloak shows its login form for the app's
                 own client and redirect address, and the app's
                 Content-Security-Policy lets the browser fetch the token
  6. users       what the operator must do so users reach this host

It does not log a user in: that needs a person's password and a browser.
A person, or the browser test in acceptance, confirms a real login.

Running it again after a failure is safe: a completed promotion is skipped,
and the later steps change nothing that is already right. A failed or partial
promotion is never retried; a person must look at the record first.
"""
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

from . import recovery, steps
from .steps import settings, target_render

APP_DR = str(settings.TOOLS_BIN / 'app_dr.py')
# Any valid S256 PKCE challenge: the login form is only shown, never submitted.
PKCE_CHALLENGE = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'


def promotion_state(host):
    """The state in the host's promotion record ('complete', 'failed', ...), or None if there is none."""
    journal = steps.promotion_record(host)
    text = host.run(['sh', '-c', 'test ! -e "$1" || cat "$1"', 'read-record', journal]).stdout
    return json.loads(text)['state'] if text.strip() else None


def promote(host, confirm_fenced, confirm_promotion):
    """Promote the database group once; True if it happened now, False if an earlier run did it."""
    state = promotion_state(host)
    if state == 'complete':
        return False
    if state is not None:
        raise RuntimeError(f'the promotion record says "{state}". Inspect every database role and '
                           f'{steps.promotion_record(host)}; failover never retries a promotion.')
    host.run(['python3', APP_DR, 'promote', '--confirm-primary-fenced', confirm_fenced,
              '--confirm-promotion', confirm_promotion], timeout=steps.STEP_TIMEOUT)
    return True


def https(host, hostname, path, *curl_options):
    """GET https://hostname:port/path from the host's own address, trusting only the host's CA."""
    script = ('curl --silent --show-error --fail --max-time 10 '
              '--cacert <(podman exec nginx cat /var/lib/platform-tls/ca.crt) '
              '--resolve "$1:$2:$3" "${@:5}" "https://$1:$2$4"')
    return host.run(['bash', '-c', script, 'https', hostname, str(settings.HTTPS_PORT), host.spec.address,
                     path, *curl_options]).stdout


def services(project_root, host, hostnames):
    """Raise unless every service is ready and each app answers over HTTPS with the host's CA."""
    wait_ready = (Path(project_root) / 'deploy/scripts/wait-ready.sh').read_text()
    waited = host.run(['bash', '-s', '--', 'app', *hostnames.values()], input=wait_ready, allowed=(0, 1))
    if waited.returncode:
        raise RuntimeError(waited.stdout.strip().splitlines()[-1] if waited.stdout.strip() else 'not ready')
    for hostname in hostnames.values():
        https(host, hostname, '/ready')


def connect_sources(headers):
    """The connect-src values of the Content-Security-Policy in HTTP response headers."""
    for line in headers.splitlines():
        name, _, value = line.partition(':')
        if name.strip().lower() == 'content-security-policy':
            for directive in value.split(';'):
                words = directive.split()
                if words and words[0] == 'connect-src':
                    return words[1:]
    return []


def login_page(host, platform, hostnames, identity):
    """Raise unless each app's login can start: Keycloak accepts its redirect, and its CSP allows the token.

    hostnames is {app name: hostname}, identity Keycloak's hostname.
    """
    identity_origin = f'https://{identity}:{settings.HTTPS_PORT}'
    for app in platform.apps:
        origin = f'https://{hostnames[app.name]}:{settings.HTTPS_PORT}'
        query = urlencode({'client_id': app.keycloak_client, 'redirect_uri': origin + '/', 'response_type': 'code',
                           'scope': 'openid', 'code_challenge': PKCE_CHALLENGE, 'code_challenge_method': 'S256'})
        try:
            page = https(host, identity, '/auth/realms/todo/protocol/openid-connect/auth?' + query)
        except RuntimeError as error:
            raise RuntimeError(f'{app.name}: Keycloak refused the login request of client '
                               f'{app.keycloak_client} with redirect {origin}/: {error}') from error
        if 'id="username"' not in page:
            raise RuntimeError(f'{app.name}: Keycloak did not show its login form for {app.keycloak_client}')
        sources = connect_sources(https(host, hostnames[app.name], '/', '--head'))
        if identity_origin not in sources:
            raise RuntimeError(f'{app.name}: Content-Security-Policy connect-src {" ".join(sources) or "(none)"} '
                               f'does not allow {identity_origin}, so the browser cannot fetch the login token')


# The TLS mode (provided, or nothing for local) and the fingerprint of the CA nginx serves from.
CA_FACTS = ('cat /var/lib/platform-tls/tls-mode 2>/dev/null; '
            'openssl x509 -in /var/lib/platform-tls/ca.crt -noout -fingerprint -sha256')


def users(host, hostnames, identity):
    """What the operator must do so users reach this host: the names, the address and, if needed, the CA to trust.

    With certificates from the organisation's CA (provided mode) clients
    already trust it, so a failover asks nothing of them; with the demo CA
    they must trust this host's new one.
    """
    lines = host.run(['podman', 'exec', 'nginx', 'sh', '-c', CA_FACTS]).stdout.strip().splitlines()
    fingerprint = lines[-1].split('=', 1)[-1] if lines else ''
    provided = 'provided' in (line.strip() for line in lines[:-1])
    names = [identity, *hostnames.values()]
    trust = ('Clients already trust your CA (provided mode), SHA-256 {}: nothing to install on them.'
             if provided else 'Have clients trust this host\'s CA, SHA-256 {}.').format(fingerprint)
    return {'hostnames': names, 'address': host.spec.address, 'ca_sha256': fingerprint,
            'client_trust': 'unchanged' if provided else 'required',
            'next': f'Point {" and ".join(names)} at {host.spec.address} (DNS or each client\'s hosts file). '
                    f'{trust} Then confirm that a user can log in to each app in a browser: failover checks '
                    'the login page, not a login.'}


def failover(project_root, controller, current, old_primary, confirm_fenced, confirm_promotion, say=None):
    """Run the steps in order on the promoted host itself; return a report with 'changed' and 'users'."""
    say = say or (lambda message: print(f'app-ops failover: {message}', file=sys.stderr, flush=True))
    if not current.spec.local:
        raise RuntimeError('failover runs on the surviving host itself: mark it local: true')
    if confirm_fenced != f'{old_primary.name} is fenced' or confirm_promotion != current.name:
        raise RuntimeError(f'confirmations must be exactly --confirm-primary-fenced "{old_primary.name} is fenced" '
                           f'--confirm-promotion {current.name}')
    recovery.require_identity(current)
    platform = steps.platform(project_root)
    report = {}
    # The public hostnames the deploy installed: the ones this host recorded as a standby.
    hostnames, identity = {}, []

    def deploy():
        changed = recovery.deploy_promoted(project_root, controller, current)
        values = json.loads(steps.target_values(current, steps.installed_pythonpath(current)))
        hostnames.update(target_render.hostnames(values, platform))
        identity.append(target_render.identity_hostname(values))
        return changed

    for name, action in (
            ('promote', lambda: promote(current, confirm_fenced, confirm_promotion)),
            ('deploy', deploy),
            ('backup', lambda: recovery.configure_backup(project_root, controller, current)),
            ('services', lambda: services(project_root, current, hostnames)),
            ('login-page', lambda: login_page(current, platform, hostnames, identity[0])),
            ('users', lambda: users(current, hostnames, identity[0]))):
        say(f'{name} ...')
        try:
            report[name] = action()
        except RuntimeError as error:
            raise RuntimeError(f'failover stopped at step "{name}": {error} -- the steps before it are done; '
                               'fix the cause, then run failover again') from error
        say('promote skipped: the promotion record already says complete'
            if name == 'promote' and report[name] is False else f'{name} done')
    return {'changed': any(report[name] for name in ('promote', 'deploy', 'backup')),
            'promoted_now': report['promote'], 'users': report['users']}
