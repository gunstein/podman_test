"""nginx's TLS certificate: the local demo CA, or a certificate the organisation's CA issued.

Two modes, chosen per host and stored in the TLS volume itself (tls-mode):

  local     the default. proxy-entrypoint.sh creates a demo CA and a server
            certificate in the volume when nginx starts. Nothing here.
  provided  the organisation's own CA (docs/TLS.md) signs a request made on
            this host, and nginx only uses what it was given:
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
volume, not even into a temporary file on the host.

check() is the nightly look at the certificate (backup nightly, tls-status):
60 days before a provided certificate expires it prepares a new request
(REQUEST_PATH), and below 30 days it reports a problem, which fails the
nightly unit.
"""
import calendar
import json
import re
import time
from pathlib import Path

from . import apps, settings, target_render
from .commands import exists, run

VOLUME = apps.SHARED_RESOURCE_OWNER.names.resource('nginx-data')
DIRECTORY = '/var/lib/todo-tls'
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
# Where check() leaves a new request for the administrator, next to the target values.
REQUEST_PATH = Path.home() / settings.DR_CONFIG / 'nginx-tls-request.csr'
REQUEST_DAYS = 60
ALERT_DAYS = 30
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


def proxy(*argv, input=None, allowed=(0,), volume=VOLUME, image=apps.PROXY_IMAGE):
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


def require_ready(volume=VOLUME, image=apps.PROXY_IMAGE):
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


def installed_hostnames(kube_runtime_dir=None):
    """The public hostnames nginx serves, as the installed shared-proxy.yaml gives them to it.

    The first is TODO_TLS_HOSTNAME, the certificate's common name. Read from
    the rendered file nginx runs, so a request always covers exactly what is
    served; the file is plain enough that no YAML parser is needed.
    """
    path = Path(kube_runtime_dir or settings.QUADLET_DIR / settings.KUBE_RUNTIME) / 'shared-proxy.yaml'
    try:
        text = path.read_text()
    except OSError as error:
        raise TlsError(f'Cannot read {path}: install the stack first (install.sh).') from error
    values = {}
    for key in ('TODO_TLS_HOSTNAME', 'APP_TLS_HOSTNAMES'):
        match = re.search(rf'^\s*{key}:\s*(".*")\s*$', text, re.M)
        if not match:
            raise TlsError(f'{path} has no {key}')
        values[key] = json.loads(match[1]).split()
    names = values['TODO_TLS_HOSTNAME'] + [name for name in values['APP_TLS_HOSTNAMES']
                                           if name not in values['TODO_TLS_HOSTNAME']]
    for name in names:
        if not re.fullmatch(r'[A-Za-z0-9.-]+', name):
            raise TlsError(f'Invalid hostname in {path}: {name!r}')
    return names


def recorded_hostnames():
    """The public hostnames this host recorded at install (target-values.json), the shared one first.

    A DR standby runs no nginx and has no shared-proxy.yaml, but it records
    the hostnames it will serve after a failover; so does an offline install.
    """
    names = target_render.hostnames(target_render.read_record())
    if apps.SHARED_RESOURCE_OWNER.name not in names:
        raise TlsError(f'{target_render.record_path()} records no public hostnames: install this host first.')
    owner = names.pop(apps.SHARED_RESOURCE_OWNER.name)
    return [owner] + [name for name in names.values() if name != owner]


def make_request(names, new_key=False, **where):
    """Write a CSR for names into the volume, with a new waiting key unless one waits already; return the CSR."""
    key = ['-key', REQUEST_KEY] if present(REQUEST_KEY, **where) and not new_key else \
        ['-newkey', KEY, '-noenc', '-keyout', REQUEST_KEY]
    proxy('openssl', 'req', '-new', *key, '-subj', f'/CN={names[0]}',
          '-addext', 'subjectAltName=' + ','.join(f'DNS:{name}' for name in names),
          '-out', REQUEST, **where)
    return proxy('cat', REQUEST, **where).stdout


def request(output, new_key=False, hostnames=None, kube_runtime_dir=None, **where):
    """tls-request: write a CSR for this host's public hostnames to output; return the hostnames.

    The private key it belongs to stays in the TLS volume (request.key) until
    tls-install puts the signed certificate next to it. Running it again
    makes a new CSR for the same waiting key, so a lost CSR file costs
    nothing; new_key starts over with a new key.
    """
    require_ready(**where)
    names = list(hostnames or installed_hostnames(kube_runtime_dir))
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


def install(certificate, ca, hostnames=None, kube_runtime_dir=None, **where):
    """tls-install: check a signed certificate and switch nginx to it; True if anything changed.

    certificate holds the server certificate first, then any intermediate
    CA certificates; ca is the organisation's root. Everything is checked
    before the volume changes (check_incoming). The volume is switched to
    provided mode first, so the entrypoint can never meet the new files in
    local mode; then nginx reloads if it runs, and must serve the new
    certificate for every hostname within SERVE_TIMEOUT seconds.
    """
    require_ready(**where)
    names = list(hostnames or installed_hostnames(kube_runtime_dir))
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
    run('podman', 'exec', CONTAINER, 'nginx', '-c', '/etc/todo-nginx/nginx.conf', '-s', 'reload')
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


VERIFY = """
set -u
for file in server.crt server.key ca.crt; do
    [ -s "$file" ] || { echo "$file is missing"; exit 1; }
done
[ "$(openssl x509 -in server.crt -noout -pubkey)" = "$(openssl pkey -in server.key -pubout)" ] ||
    { echo 'server.key does not belong to server.crt'; exit 1; }
for name in "$@"; do
    openssl verify -no_check_time -CAfile ca.crt -untrusted server.crt -verify_hostname "$name" server.crt \
        >/dev/null 2>&1 || { echo "server.crt is not valid for $name from ca.crt"; exit 1; }
done
openssl x509 -in server.crt -noout -enddate
"""


def status(names, **where):
    """How nginx would start with this volume: (mode, days left, problem or '').

    In provided mode it is the entrypoint's own check, in one throwaway
    container: the files are there, the key fits, and the certificate names
    every one of names from ca.crt; then the days it has left. A volume
    without a certificate is ('local', None, '').
    """
    if not exists('volume', where.get('volume', VOLUME)) or not present('server.crt', **where):
        return 'local', None, ''
    current = mode(**where)
    if current != PROVIDED:
        days = days_until(proxy('openssl', 'x509', '-in', 'server.crt', '-noout', '-enddate', **where).stdout)
        return current, days, ''
    result = proxy('sh', '-c', VERIFY, 'verify', *names, allowed=(0, 1), **where)
    if result.returncode:
        return current, None, result.stdout.strip() or 'the provided certificate does not fit'
    return current, days_until(result.stdout), ''


def check(kube_runtime_dir=None, hostnames=None, **where):
    """The nightly look at nginx's certificate; return (lines, problems).

    Provided mode: from REQUEST_DAYS before expiry a request is prepared,
    with a new key unless a request already waits (so a renewal also
    replaces the key), and copied to REQUEST_PATH for the administrator;
    below ALERT_DAYS it is a problem.
    Local mode: the entrypoint renews its demo certificate only when nginx
    starts, so below ALERT_DAYS the advice is a restart.
    """
    provided = exists('volume', where.get('volume', VOLUME)) and mode(**where) == PROVIDED
    names = list(hostnames or installed_hostnames(kube_runtime_dir)) if provided else []
    current, days, problem = status(names, **where)
    if problem:
        return [], [f'the nginx certificate would stop nginx at its next start: {problem}']
    if days is None:
        return [], []
    lines = [f'nginx certificate ({current} mode): valid {days} more days']
    if current != PROVIDED:
        if days < ALERT_DAYS:
            return lines, [f'the nginx demo certificate expires in {days} days; '
                           'systemctl --user restart shared-proxy.service renews it']
        return lines, []
    if days < REQUEST_DAYS:
        if not present(REQUEST, **where):
            require_ready(**where)
            make_request(names, new_key=True, **where)
        REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        REQUEST_PATH.write_text(proxy('cat', REQUEST, **where).stdout)
        lines.append(f'A request for the next certificate is ready: {REQUEST_PATH}. Have the CA sign it, '
                     'then: python3 -m app_installer tls-install --certificate FILE --ca FILE')
    if days < ALERT_DAYS:
        return lines, [f'the nginx certificate expires in {days} days; install the next one '
                       f'(the request is {REQUEST_PATH})']
    return lines, []
