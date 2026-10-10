"""nginx's TLS certificate: the local demo CA, or a certificate a separate CA process issued.

Two modes, chosen per host and stored in the TLS volume itself (tls-mode):

  local     the default. The shared-proxy pod's init container (nginx-tls,
            proxy-entrypoint.sh) creates a demo CA and a server certificate
            in the volume. Nothing here.
  provided  a CA process outside nginx's security domain signs a request
            made on this host, and nginx only uses what it was given. That
            CA (deploy/scripts/app_ca.py, docs/TLS.md) may run on this same
            host, from its own storage: its key never enters the volume.
              tls-request  a new private key in the volume and a certificate
                           signing request (CSR) for every public hostname;
                           only the CSR leaves the host
              tls-install  checks the signed certificate against the CA, the
                           hostnames and the waiting key before anything
                           changes, then switches the volume to provided mode
                           and reloads nginx (no restart)
            proxy-entrypoint.sh then never issues a certificate and never falls
            back to the demo CA: a missing or wrong file stops nginx.

Every openssl command runs in a throwaway container of the proxy image, with
the TLS volume mounted and no network, as the nginx user that owns the
volume. The host needs no openssl, and the private key never leaves the
volume, not even into a temporary file on the host. Only these containers and
the init container write the volume; nginx mounts it read-only.

A renewal never touches the active pair (server.key, server.crt): the new
key waits as request.key until its signed certificate passed every check,
and only then becomes server.key. nginx reads the pair only when it starts or
reloads, and the reload comes after the switch.

check() is the nightly look at the certificate (backup nightly, tls-status):
below 30 days a problem fails the nightly unit, in time for the CA step.
Hostnames always come from this host's record (target-values.json), which
every install writes.
"""
import calendar
import json
import time
from pathlib import Path

from . import apps, target_render
from .commands import exists, run

DIRECTORY = '/var/lib/platform-tls'
CONTAINER = 'nginx'
MODE_FILE = 'tls-mode'
PROVIDED = 'provided'
# The proxy image says which modes its entrypoint knows (proxy/Containerfile).
MODES_LABEL = 'io.todo.proxy.tls'
# The waiting key and request of tls-request; tls-install makes the key server.key.
REQUEST_KEY = 'request.key'
# RSA, for the widest choice of CAs and clients.
KEY = 'rsa:3072'
REQUEST = 'request.csr'
ALERT_DAYS = 30
# The proxy image's entrypoint; its check role is status().
ENTRYPOINT = '/usr/local/bin/proxy-entrypoint'
# Seconds tls-install waits for nginx to serve the new certificate after a reload.
SERVE_TIMEOUT = 10


class TlsError(RuntimeError):
    """A certificate, key or CA that does not fit, or a host that is not ready for it."""


def days_until(enddate):
    """Whole days until openssl's 'notAfter=...' line; negative once it has passed.

    openssl prints it as notAfter=Oct  7 12:00:00 2028 GMT, always in English
    and in UTC.
    """
    expires = calendar.timegm(time.strptime(enddate.strip().split('=', 1)[1], '%b %d %H:%M:%S %Y %Z'))
    return int((expires - time.time()) // 86400)


def proxy(*argv, input=None, allowed=(0,), volume=apps.NGINX_TLS_VOLUME, image=apps.PROXY_IMAGE):
    """Run argv in a throwaway proxy container with the TLS volume at DIRECTORY; no network.

    Commands that write a file run under umask 077, so a key is never
    readable by anyone but the nginx user.
    """
    # stdin is attached only for a file to write, never the operator's terminal.
    attach = ['--interactive'] if input is not None else []
    return run('podman', 'run', '--rm', *attach, '--network', 'none', '--user', '101:101',
               '--volume', f'{volume}:{DIRECTORY}', '--workdir', DIRECTORY, '--entrypoint', '/bin/sh',
               image, '-c', 'umask 077 && exec "$@"', 'tls', *argv, input=input, allowed=allowed,
               description=f'TLS step {argv[0]} {argv[1] if len(argv) > 1 else ""}'.strip())


def present(name, **where):
    """True if the TLS volume holds a non-empty file name."""
    return proxy('test', '-s', name, allowed=(0, 1), **where).returncode == 0


def write(name, text, **where):
    """Replace the file name in the TLS volume with text, atomically."""
    proxy('sh', '-c', 'cat > "$1.new" && mv "$1.new" "$1"', 'write', name, input=text, **where)


def mode(**where):
    """'provided' or 'local': how this host's nginx gets its certificate."""
    if present(MODE_FILE, **where):
        return proxy('cat', MODE_FILE, **where).stdout.strip()
    return 'local'


def require_ready(volume=apps.NGINX_TLS_VOLUME, image=apps.PROXY_IMAGE):
    """Refuse a host without the TLS volume, or with a proxy image that does not know provided mode.

    The volume is created by the first install. An older proxy image would
    see no demo CA key in provided mode and replace the certificate with a
    new demo CA, which is exactly the silent fallback provided mode forbids.
    """
    if not exists('volume', volume):
        raise TlsError(f'The TLS volume {volume} does not exist: install the stack first (install.sh).')
    inspection = json.loads(run('podman', 'image', 'inspect', image).stdout)
    modes = ((inspection[0].get('Labels') or {}).get(MODES_LABEL) or '').split()
    if PROVIDED not in modes:
        raise TlsError(f'The proxy image {image} does not know provided TLS mode. Rebuild it '
                       '(install --refresh-images) or load it from a current offline bundle.')


def recorded_hostnames():
    """The public hostnames this host recorded at install (target-values.json), the shared one first.

    The first is the certificate's common name. A DR standby runs no nginx,
    but it records the hostnames it will serve after a failover.
    """
    names = target_render.hostnames(target_render.read_record())
    if apps.IDENTITY_APP.name not in names:
        raise TlsError(f'{target_render.record_path()} records no public hostnames: install this host first.')
    owner = names.pop(apps.IDENTITY_APP.name)
    return [owner] + [name for name in names.values() if name != owner]


def make_request(names, new_key=False, **where):
    """Write a CSR for names into the volume, with a new waiting key unless one waits already; return the CSR."""
    key = ['-key', REQUEST_KEY] if present(REQUEST_KEY, **where) and not new_key else \
        ['-newkey', KEY, '-noenc', '-keyout', REQUEST_KEY]
    proxy('openssl', 'req', '-new', *key, '-subj', f'/CN={names[0]}',
          '-addext', 'subjectAltName=' + ','.join(f'DNS:{name}' for name in names),
          '-out', REQUEST, **where)
    return proxy('cat', REQUEST, **where).stdout


def request(output, new_key=False, hostnames=None, **where):
    """tls-request: write a CSR for this host's public hostnames to output; return the hostnames.

    The private key it belongs to stays in the TLS volume (request.key) until
    tls-install puts the signed certificate next to it. Running it again
    makes a new CSR for the same waiting key, so a lost CSR file costs
    nothing; new_key starts over with a new key.
    """
    require_ready(**where)
    names = list(hostnames or recorded_hostnames())
    output = Path(output)
    output.write_text(make_request(names, new_key, **where))
    output.chmod(0o644)
    return names


def public_key(name, **where):
    """The PEM public key of a certificate (*.crt) or a private key (*.key) in the volume; '' if unreadable."""
    if name.endswith('.crt'):
        argv = ('openssl', 'x509', '-in', name, '-noout', '-pubkey')
    else:
        argv = ('openssl', 'pkey', '-in', name, '-pubout')
    result = proxy(*argv, allowed=(0, 1), **where)
    return result.stdout.strip() if result.returncode == 0 else ''


def fingerprint(name, **where):
    """The SHA-256 fingerprint of the first certificate in a file in the volume."""
    return proxy('openssl', 'x509', '-in', name, '-noout', '-fingerprint', '-sha256', **where).stdout.strip()


def check_incoming(names, **where):
    """Check incoming.crt and incoming-ca.crt in the volume; return the key file the certificate belongs to.

    The CA must be a self-signed root. The certificate (the first in
    incoming.crt; any intermediates follow it) must chain to that root, be
    valid now for TLS servers, and name every hostname. Its key must be the
    waiting request.key, or server.key when the same key is renewed.
    """
    if proxy('openssl', 'verify', '-CAfile', 'incoming-ca.crt', 'incoming-ca.crt',
             allowed=(0, 2), **where).returncode:
        raise TlsError('The CA file is not a self-signed root certificate.')
    for name in names:
        result = proxy('openssl', 'verify', '-CAfile', 'incoming-ca.crt', '-untrusted', 'incoming.crt',
                       '-purpose', 'sslserver', '-verify_hostname', name, 'incoming.crt',
                       allowed=(0, 2), **where)
        if result.returncode:
            reason = (result.stderr or result.stdout).strip().splitlines()[-1:] or ['no reason given']
            raise TlsError(f'The certificate is not valid for {name} from this CA: {reason[0]}')
    certificate = public_key('incoming.crt', **where)
    for key in (REQUEST_KEY, 'server.key'):
        if certificate and present(key, **where) and public_key(key, **where) == certificate:
            return key
    raise TlsError('The certificate belongs to neither the waiting request (tls-request) '
                   'nor the installed key; was it issued for another host?')


ACTIVATE = """
printf '%s\\n' provided > tls-mode.new && mv tls-mode.new tls-mode
if [ "$1" != server.key ]; then mv "$1" server.key; fi
mv incoming.crt server.crt
mv incoming-ca.crt ca.crt
chmod 0644 server.crt ca.crt
rm -f request.csr ca.key ca.srl
"""


def install(certificate, ca, hostnames=None, **where):
    """tls-install: check a signed certificate and switch nginx to it; True if anything changed.

    certificate holds the server certificate first, then any intermediate
    CA certificates; ca is the organisation's root. Everything is checked
    before the volume changes (check_incoming). The volume is switched to
    provided mode first, so the entrypoint can never meet the new files in
    local mode; then nginx reloads if it runs, and must serve the new
    certificate for every hostname within SERVE_TIMEOUT seconds.
    """
    require_ready(**where)
    names = list(hostnames or recorded_hostnames())
    chain, root = Path(certificate).read_text(), Path(ca).read_text()
    for path, text in ((certificate, chain), (ca, root)):
        if '-----BEGIN CERTIFICATE-----' not in text or 'PRIVATE KEY' in text:
            raise TlsError(f'{path} must hold PEM certificates only, never a private key.')
    if mode(**where) == PROVIDED and present('server.crt', **where) and \
            proxy('cat', 'server.crt', **where).stdout == chain and proxy('cat', 'ca.crt', **where).stdout == root:
        return False
    write('incoming.crt', chain, **where)
    write('incoming-ca.crt', root, **where)
    try:
        key = check_incoming(names, **where)
        expected = fingerprint('incoming.crt', **where)
        proxy('sh', '-c', ACTIVATE, 'activate', key, **where)
    finally:
        proxy('rm', '-f', 'incoming.crt', 'incoming-ca.crt', **where)
    reload(names, expected)
    return True


SERVED = ('openssl s_client -connect 127.0.0.1:8443 -servername "$1" </dev/null 2>/dev/null'
          ' | openssl x509 -noout -fingerprint -sha256')


def reload(names, expected):
    """Reload a running nginx and wait until it serves the certificate expected for every name.

    A stopped nginx reads the files when it next starts; nothing to do.
    """
    if not exists('container', CONTAINER) or run(
            'podman', 'inspect', '--format', '{{.State.Running}}', CONTAINER).stdout.strip() != 'true':
        return
    run('podman', 'exec', CONTAINER, 'nginx', '-c', '/etc/platform-nginx/nginx.conf', '-s', 'reload')
    for name in names:
        for _ in range(SERVE_TIMEOUT):
            served = run('podman', 'exec', CONTAINER, 'sh', '-c', SERVED, 'served', name,
                         allowed=(0, 1)).stdout.strip()
            if served == expected:
                break
            time.sleep(1)
        else:
            raise TlsError(f'nginx does not serve the new certificate for {name}; '
                           'see journalctl --user -u shared-proxy.service')


def status(names, **where):
    """How nginx would start with this volume: (mode, days left, problem or '').

    It runs the entrypoint's own check (PLATFORM_TLS_ROLE=check), the one nginx
    runs before it serves: the files are there, the key fits, and the
    certificate names every one of names from ca.crt. A volume without a
    certificate is ('local', None, '').
    """
    if not exists('volume', where.get('volume', apps.NGINX_TLS_VOLUME)) or not present('server.crt', **where):
        return 'local', None, ''
    result = proxy('env', 'PLATFORM_TLS_ROLE=check', 'PLATFORM_TLS_DIRECTORY=.', f'PLATFORM_TLS_HOSTNAME={names[0]}',
                   'APP_TLS_HOSTNAMES=' + ' '.join(names), ENTRYPOINT, allowed=(0, 1), **where)
    output = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode:
        errors = [line.removeprefix('ERROR: ') for line in result.stderr.splitlines() if line.startswith('ERROR: ')]
        return output.get('mode', mode(**where)), None, (errors or ['the certificate does not fit'])[-1]
    return output['mode'], days_until('notAfter=' + output['notAfter']), ''


def check(hostnames=None, **where):
    """The nightly look at nginx's certificate; return (lines, problems).

    Below ALERT_DAYS it is a problem: in provided mode the next certificate
    needs the CA step (tls-request, sign, tls-install); in local mode the
    entrypoint renews its demo certificate only when nginx starts.
    """
    if not exists('volume', where.get('volume', apps.NGINX_TLS_VOLUME)):
        return [], []
    current, days, problem = status(list(hostnames or recorded_hostnames()), **where)
    if problem:
        return [], [f'the nginx certificate would stop nginx at its next start: {problem}']
    if days is None:
        return [], []
    lines = [f'nginx certificate ({current} mode): valid {days} more days']
    if days >= ALERT_DAYS:
        return lines, []
    if current == PROVIDED:
        return lines, [f'the nginx certificate expires in {days} days; run tls-request, have the CA sign '
                       'the request (app_ca.py sign, or sudo platform-ca-sign), then tls-install']
    return lines, [f'the nginx demo certificate expires in {days} days; '
                   'systemctl --user restart shared-proxy.service renews it']
