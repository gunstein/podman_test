"""nginx's TLS certificate as Podman secrets: the local demo CA, or a certificate a separate CA process issued.

The same two modes and commands as tls.py, which keeps the files in the TLS
volume platform-nginx-data; here every file is a Podman secret on this host.
tls_store.py picks one of the two (settings.NGINX_TLS_STORAGE), and tls.py
stays, so a host can go back to the volume (docs/TLS.md).

  one raw secret per file (apps.PROXY_TLS_SECRETS)
    tls-mode     local or provided
    ca.crt       the root clients trust
    server.crt   nginx's certificate, then any intermediate CA certificates
    server.key   its private key
    ca.key       the demo CA's key; local mode only, never given to nginx
    request.key  a key waiting for its certificate (tls-request)
  and the Kube secret nginx mounts (apps.PROXY_KUBE_TLS_SECRET): tls-mode,
  ca.crt, server.crt and server.key, made from those four raw secrets.

How a file becomes a secret, and a secret becomes a file again:

- openssl runs in a throwaway container of the proxy image, with no network,
  as the nginx user (101), working in a tmpfs. It prints a new key or
  certificate on stdout, and `podman secret create NAME -` stores what it
  printed. No key is ever written to a file on the host.
- The secrets a step needs come into that container as files:
  `podman run --secret NAME,type=mount,target=/run/platform-tls/FILE,uid=101,gid=101,mode=0400`.
- nginx gets its files from the Kube secret: `podman kube play` mounts it
  read-only at /var/lib/platform-tls, one file per data key
  (deploy/manifests/shared-proxy.yaml.j2). Its entrypoint checks them as for
  the volume (PLATFORM_TLS_ROLE=serve) and never writes.

  local     provision() makes the demo CA and a leaf certificate for every
            public hostname before nginx starts (install, deploy-promoted,
            tls-renew), and replaces either when it is missing, expires
            within 30 days or no longer fits: the volume's init container
            did this at every pod start.
  provided  tls-request makes request.key and prints a CSR; tls-install
            checks the signed certificate against the CA, the hostnames and
            the waiting key before anything changes, then switches.

Podman copies a secret into a container when the container is created: a
running nginx keeps the files it started with, reload or not. So every
change goes to the raw secrets first and to the Kube secret last, and then
shared-proxy.service restarts, which recreates the pod. A new certificate
therefore costs a few seconds without HTTPS, where the volume needed only a
reload; it never touches the active pair before the new one passed every
check.
"""
import base64
import json
import secrets as random
import time
from pathlib import Path

from . import apps, secrets, tls
from .commands import exists, run

FILES = apps.PROXY_TLS_SECRETS
KUBE_SECRET = apps.PROXY_KUBE_TLS_SECRET
# What nginx mounts, in this order: the Kube secret's data keys.
SERVED = ('tls-mode', 'ca.crt', 'server.crt', 'server.key')
# Where a throwaway container sees the secrets it was given.
MOUNT = '/run/platform-tls'
SERVICE = 'shared-proxy.service'
CONTAINER = tls.CONTAINER
PROVIDED = tls.PROVIDED
ALERT_DAYS = tls.ALERT_DAYS
TlsError = tls.TlsError
recorded_hostnames = tls.recorded_hostnames
# RSA, as in tls.py and the entrypoint, for the widest choice of CAs and clients.
KEY_BITS = 3072
CA_DAYS = 3650
LEAF_DAYS = 397
RENEW_SECONDS = 30 * 24 * 3600
ENTRYPOINT = tls.ENTRYPOINT
# Seconds after a restart until nginx must serve the new certificate.
SERVE_TIMEOUT = 30


def path(name):
    """Where a throwaway container sees the secret for file name."""
    return f'{MOUNT}/{name}'


def proxy(*argv, files=(), input=None, allowed=(0,), secret_output=False, image=None):
    """Run argv in a throwaway proxy container with the secrets for files at MOUNT; no network.

    Each file is a Podman secret mounted read-only for the nginx user only
    (mode 0400). The container's own work goes to a tmpfs under umask 077.
    secret_output keeps stdout, which then holds a private key, out of any
    error message.
    """
    mounts = []
    for name in files:
        mounts += ['--secret', f'{FILES[name]},type=mount,target={path(name)},uid=101,gid=101,mode=0400']
    # stdin is attached only for input, never the operator's terminal.
    attach = ['--interactive'] if input is not None else []
    return run('podman', 'run', '--rm', *attach, '--network', 'none', '--user', '101:101',
               '--tmpfs', '/work:mode=1777', '--workdir', '/work', *mounts, '--entrypoint', '/bin/sh',
               image or apps.PROXY_IMAGE, '-c', 'umask 077 && exec "$@"', 'tls', *argv, input=input, allowed=allowed,
               secret_output=secret_output,
               description=f'TLS step {argv[0]} {argv[1] if len(argv) > 1 else ""}'.strip())


def has(name):
    """True if the secret for file name exists."""
    return exists('secret', FILES[name])


def read(name):
    """The text of file name, ending in one newline (secrets.read strips it)."""
    return secrets.read(FILES[name]) + '\n'


def store(name, text):
    """Make text the secret for file name, replacing an existing one.

    `podman secret create --replace` refuses a secret that does not exist
    yet (Podman 4.9), so --replace is passed only when it does.
    """
    secret = FILES[name]
    run('podman', 'secret', 'create', *(['--replace'] if exists('secret', secret) else []), secret, '-',
        input=text.rstrip('\n') + '\n')


def remove(name):
    """Remove the secret for file name if it exists."""
    if has(name):
        run('podman', 'secret', 'rm', FILES[name])


def mode():
    """'provided' or 'local': how this host's nginx gets its certificate."""
    return read('tls-mode').strip() if has('tls-mode') else 'local'


def require_ready(image=None):
    """Refuse a proxy image that does not know provided mode (see tls.require_ready); no volume is needed."""
    image = image or apps.PROXY_IMAGE
    inspection = json.loads(run('podman', 'image', 'inspect', image).stdout)
    modes = ((inspection[0].get('Labels') or {}).get(tls.MODES_LABEL) or '').split()
    if PROVIDED not in modes:
        raise TlsError(f'The proxy image {image} does not know provided TLS mode. Rebuild it '
                       '(install --refresh-images) or load it from a current offline bundle.')


def new_key():
    """A new RSA private key, made in a throwaway container; only stdout carries it, to a secret."""
    return proxy('openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', f'rsa_keygen_bits:{KEY_BITS}',
                 secret_output=True).stdout


# The certificate requests and checks, as shell scripts for one throwaway
# container each. "$1" is MOUNT; the rest are their own arguments.

SIGN = """
d=$1 days=$2 serial=$3 subject=$4 names=$5
printf '%s\\n' "subjectAltName=$names" basicConstraints=critical,CA:FALSE \\
    keyUsage=critical,digitalSignature,keyEncipherment extendedKeyUsage=serverAuth > server.ext
openssl req -new -key "$d/server.key" -subj "/CN=$subject" -out server.csr
openssl x509 -req -in server.csr -CA "$d/ca.crt" -CAkey "$d/ca.key" -set_serial "0x$serial" \\
    -days "$days" -sha256 -extfile server.ext
"""

LEAF_FITS = """
d=$1 seconds=$2
shift 2
openssl x509 -in "$d/server.crt" -noout -checkend "$seconds" >/dev/null || exit 1
[ "$(openssl x509 -in "$d/server.crt" -noout -pubkey)" = "$(openssl pkey -in "$d/server.key" -pubout)" ] || exit 1
for name in "$@"; do
    openssl verify -CAfile "$d/ca.crt" -verify_hostname "$name" "$d/server.crt" >/dev/null 2>&1 || exit 1
done
"""

# tls.check_incoming as one script: prints the key file the certificate belongs to.
INCOMING = """
d=$1
shift
if ! openssl verify -CAfile "$d/incoming-ca.crt" "$d/incoming-ca.crt" >/dev/null 2>&1; then
    echo 'ERROR: The CA file is not a self-signed root certificate.' >&2
    exit 2
fi
for name in "$@"; do
    if ! result=$(openssl verify -CAfile "$d/incoming-ca.crt" -untrusted "$d/incoming.crt" \\
        -purpose sslserver -verify_hostname "$name" "$d/incoming.crt" 2>&1); then
        echo "ERROR: The certificate is not valid for $name from this CA: $(printf '%s\\n' "$result" | tail -n 1)" >&2
        exit 2
    fi
done
certificate=$(openssl x509 -in "$d/incoming.crt" -noout -pubkey)
for key in request.key server.key; do
    if [ -s "$d/$key" ] && [ "$(openssl pkey -in "$d/$key" -pubout)" = "$certificate" ]; then
        echo "$key"
        exit 0
    fi
done
echo 'ERROR: The certificate belongs to neither the waiting request (tls-request) nor the installed key; was it issued for another host?' >&2
exit 2
"""


def ca_fits():
    """True if the demo CA's key and certificate exist and the certificate lasts 30 more days."""
    if not (has('ca.key') and has('ca.crt')):
        return False
    return not proxy('openssl', 'x509', '-in', path('ca.crt'), '-noout', '-checkend', str(RENEW_SECONDS),
                     files=('ca.crt',), allowed=(0, 1)).returncode


def leaf_fits(names):
    """True if server.crt is from ca.crt, names every hostname, fits server.key and lasts 30 more days."""
    if not all(has(name) for name in ('ca.crt', 'server.crt', 'server.key')):
        return False
    return not proxy('sh', '-c', LEAF_FITS, 'leaf', MOUNT, str(RENEW_SECONDS), *names,
                     files=('ca.crt', 'server.crt', 'server.key'), allowed=(0, 1)).returncode


def make_ca():
    """A new demo CA: its key and its self-signed certificate (10 years)."""
    store('ca.key', new_key())
    store('ca.crt', proxy('openssl', 'req', '-x509', '-key', path('ca.key'), '-days', str(CA_DAYS), '-sha256',
                          '-subj', '/CN=Todo Demo Local Root CA',
                          '-addext', 'basicConstraints=critical,CA:TRUE',
                          '-addext', 'keyUsage=critical,keyCertSign,cRLSign', files=('ca.key',)).stdout)


def issue(names):
    """A new leaf key and a certificate for names from the demo CA (397 days); names[0] is the subject."""
    store('server.key', new_key())
    store('server.crt', proxy('sh', '-c', SIGN, 'sign', MOUNT, str(LEAF_DAYS), random.token_hex(16), names[0],
                              ','.join(f'DNS:{name}' for name in names),
                              files=('ca.crt', 'ca.key', 'server.key')).stdout)


def adopt_volume():
    """Copy the TLS volume's files into secrets once, so a host that served from it keeps its CA; True if done.

    Only when no served file is a secret yet and the volume holds a
    certificate. It reads the volume through tls.py, which stays for this
    and for going back.
    """
    if any(has(name) for name in SERVED) or not exists('volume', apps.NGINX_TLS_VOLUME) or not tls.present('server.crt'):
        return False
    for name in ('ca.crt', 'server.crt', 'server.key', 'ca.key', 'request.key'):
        if tls.present(name):
            # stdout holds a private key for the *.key files: never show it in an error.
            store(name, run('podman', 'run', '--rm', '--network', 'none', '--user', '101:101', '--volume',
                            f'{apps.NGINX_TLS_VOLUME}:{tls.DIRECTORY}:ro', '--entrypoint', 'cat', apps.PROXY_IMAGE,
                            f'{tls.DIRECTORY}/{name}', secret_output=True).stdout)
    store('tls-mode', tls.mode() + '\n')
    return True


def publish():
    """Make the Kube secret nginx mounts hold the four served files; True if it changed.

    The data keys become the file names under /var/lib/platform-tls in nginx.
    """
    data = {name: base64.b64encode(read(name).encode()).decode() for name in SERVED}
    present = exists('secret', KUBE_SECRET)
    if present:
        try:
            if json.loads(secrets.read(KUBE_SECRET)).get('data') == data:
                return False
        except (ValueError, AttributeError):
            pass
    payload = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': KUBE_SECRET}, 'data': data}
    run('podman', 'secret', 'create', *(['--replace'] if present else []), KUBE_SECRET, '-',
        input=json.dumps(payload))
    return True


def provision(hostnames=None):
    """Give nginx its files as secrets before it starts; True if what nginx mounts changed.

    install and deploy-promoted call it before shared-proxy starts, and a
    True result means a running nginx must restart. In local mode it is the
    volume's init container: a demo CA, and a leaf for hostnames
    (Keycloak's first), each replaced when missing, within 30
    days of its end or no longer fitting. In provided mode it issues
    nothing: the files tls-install put there must all exist.
    """
    require_ready()
    names = list(hostnames or recorded_hostnames())
    changed = adopt_volume()
    if mode() == PROVIDED:
        missing = [name for name in SERVED if not has(name)]
        if missing:
            raise TlsError('Provided TLS mode, but ' + ', '.join(missing) + ' is missing; '
                           'run tls-request and tls-install again.')
    else:
        new_ca = not ca_fits()
        if new_ca:
            make_ca()
        if new_ca or not leaf_fits(names):
            issue(names)
            changed = True
        if not has('tls-mode'):
            store('tls-mode', 'local\n')
    return publish() or changed


def ordered(hostnames, identity):
    """The list provision() takes: Keycloak's hostname first, then each app's {app name: hostname}, no repeats."""
    return [identity] + [name for name in dict.fromkeys(hostnames.values()) if name != identity]


def make_request(names, new_key_wanted=False):
    """A CSR for names, with a new waiting key (request.key) unless one waits already; return the CSR."""
    if new_key_wanted or not has('request.key'):
        store('request.key', new_key())
    return proxy('openssl', 'req', '-new', '-key', path('request.key'), '-subj', f'/CN={names[0]}',
                 '-addext', 'subjectAltName=' + ','.join(f'DNS:{name}' for name in names),
                 files=('request.key',)).stdout


def request(output, new_key=False, hostnames=None):
    """tls-request: write a CSR for this host's public hostnames to output; return the hostnames.

    Its private key waits in the secret request.key until tls-install puts
    the signed certificate next to it. Running it again makes a new CSR for
    the same waiting key; new_key starts over with a new key.
    """
    require_ready()
    names = list(hostnames or recorded_hostnames())
    output = Path(output)
    output.write_text(make_request(names, new_key))
    output.chmod(0o644)
    return names


def check_incoming(names):
    """Check incoming.crt and incoming-ca.crt (see tls.check_incoming); return the key file it belongs to."""
    files = ['incoming.crt', 'incoming-ca.crt'] + [key for key in ('request.key', 'server.key') if has(key)]
    result = proxy('sh', '-c', INCOMING, 'incoming', MOUNT, *names, files=files, allowed=(0, 2))
    if result.returncode:
        errors = [line.removeprefix('ERROR: ') for line in result.stderr.splitlines() if line.startswith('ERROR: ')]
        raise TlsError((errors or ['The certificate does not fit.'])[-1])
    return result.stdout.strip()


def install(certificate, ca, hostnames=None):
    """tls-install: check a signed certificate and switch nginx to it; True if anything changed.

    certificate holds the server certificate first, then any intermediate
    CA certificates; ca is the organisation's root. Both are checked as
    temporary secrets (incoming.crt, incoming-ca.crt) before anything else
    changes. Then the raw secrets switch to provided mode, the demo CA's key
    and the waiting key go, the Kube secret follows, and a running nginx
    restarts and must serve the new certificate for every hostname.
    """
    require_ready()
    names = list(hostnames or recorded_hostnames())
    chain, root = Path(certificate).read_text(), Path(ca).read_text()
    for source, text in ((certificate, chain), (ca, root)):
        if '-----BEGIN CERTIFICATE-----' not in text or 'PRIVATE KEY' in text:
            raise TlsError(f'{source} must hold PEM certificates only, never a private key.')
    chain, root = chain.rstrip('\n') + '\n', root.rstrip('\n') + '\n'
    if mode() == PROVIDED and has('server.crt') and read('server.crt') == chain and read('ca.crt') == root:
        return False
    store('incoming.crt', chain)
    store('incoming-ca.crt', root)
    try:
        key = check_incoming(names)
        expected = proxy('openssl', 'x509', '-in', path('incoming.crt'), '-noout', '-fingerprint', '-sha256',
                         files=('incoming.crt',)).stdout.strip()
    finally:
        remove('incoming.crt')
        remove('incoming-ca.crt')
    if key == 'request.key':
        store('server.key', read('request.key'))
    store('server.crt', chain)
    store('ca.crt', root)
    store('tls-mode', PROVIDED + '\n')
    publish()
    remove('request.key')
    remove('ca.key')
    restart(names, expected)
    return True


SERVED_FINGERPRINT = tls.SERVED


def restart(names, expected):
    """Restart nginx so it mounts the new Kube secret, and wait until it serves expected for every name.

    Only a running shared-proxy.service restarts; a host without it (a
    standby) reads the secret when nginx first starts. A development nginx
    (podman kube play, no systemd) must be started again by hand.
    """
    active = run('systemctl', '--user', 'is-active', SERVICE, allowed=(0, 1, 2, 3, 4)).stdout.strip() == 'active'
    if not active:
        if exists('container', CONTAINER):
            raise TlsError('The certificate is installed, but nginx runs without systemd (development): '
                           'start it again with deploy/scripts/dev/dev-up.sh to serve it.')
        return
    run('systemctl', '--user', 'restart', SERVICE)
    for name in names:
        for _ in range(SERVE_TIMEOUT):
            served = run('podman', 'exec', CONTAINER, 'sh', '-c', SERVED_FINGERPRINT, 'served', name,
                         allowed=(0, 1, 125, 126)).stdout.strip()
            if served == expected:
                break
            time.sleep(1)
        else:
            raise TlsError(f'nginx does not serve the new certificate for {name}; '
                           'see journalctl --user -u shared-proxy.service')


def fingerprint():
    """The SHA-256 fingerprint of server.crt."""
    return proxy('openssl', 'x509', '-in', path('server.crt'), '-noout', '-fingerprint', '-sha256',
                 files=('server.crt',)).stdout.strip()


def renew(hostnames=None):
    """tls-renew: provision(), then restart a running nginx if what it mounts changed; True if so.

    In local mode this replaces a demo certificate near its end, which the
    volume's init container did at every pod start. In provided mode the
    next certificate needs the CA: tls-request, sign, tls-install.
    """
    names = list(hostnames or recorded_hostnames())
    changed = provision(names)
    if changed:
        restart(names, fingerprint())
    return changed


def status(names):
    """How nginx would start with these secrets: (mode, days left, problem or ''); see tls.status.

    It runs the entrypoint's own check (PLATFORM_TLS_ROLE=check) on the raw
    secrets, mounted as the files nginx would get. No certificate yet is
    ('local', None, '').
    """
    if not has('server.crt'):
        return 'local', None, ''
    result = proxy('env', 'PLATFORM_TLS_ROLE=check', f'PLATFORM_TLS_DIRECTORY={MOUNT}', f'PLATFORM_TLS_HOSTNAME={names[0]}',
                   'APP_TLS_HOSTNAMES=' + ' '.join(names), ENTRYPOINT,
                   files=[name for name in SERVED if has(name)], allowed=(0, 1))
    output = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode:
        errors = [line.removeprefix('ERROR: ') for line in result.stderr.splitlines() if line.startswith('ERROR: ')]
        return output.get('mode', mode()), None, (errors or ['the certificate does not fit'])[-1]
    return output['mode'], tls.days_until('notAfter=' + output['notAfter']), ''


def check(hostnames=None):
    """The nightly look at nginx's certificate; return (lines, problems), as tls.check.

    Below ALERT_DAYS it is a problem: in provided mode the next certificate
    needs the CA step; in local mode tls-renew (or the next install) renews
    the demo certificate.
    """
    if not has('server.crt'):
        return [], []
    current, days, problem = status(list(hostnames or recorded_hostnames()))
    if problem:
        return [], [f'the nginx certificate would stop nginx at its next start: {problem}']
    if days is None:
        return [], []
    lines = [f'nginx certificate ({current} mode, Podman secrets): valid {days} more days']
    if days >= ALERT_DAYS:
        return lines, []
    if current == PROVIDED:
        return lines, [f'the nginx certificate expires in {days} days; run tls-request, have the CA sign '
                       'the request (app_ca.py sign, or sudo platform-ca-sign), then tls-install']
    return lines, [f'the nginx demo certificate expires in {days} days; '
                   'python3 -m app_installer tls-renew renews it']


def secret_names():
    """Every Podman secret this module may create: one per file, and the Kube secret."""
    return [*FILES.values(), KUBE_SECRET]
