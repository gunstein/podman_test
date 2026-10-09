"""A small CA for nginx's certificates (provided TLS mode, docs/TLS.md).

Provided mode means that a CA process outside nginx's security domain issues
nginx's certificate. That CA can run on the same host as Podman and nginx;
what must stay apart is its storage. The CA directory holds

  ca.key      the CA's private key, encrypted with a passphrase
  ca.crt      the root certificate every client trusts once
  issued.log  one line per certificate issued

and never goes near Podman: it is not a volume, not mounted into nginx, and
not the TLS volume. The CA only ever sees a certificate signing request
(CSR); nginx's private key stays in the TLS volume where tls-request made it.

Two ways to run it (docs/TLS.md says which security each gives):

  development: the same Unix user that runs rootless Podman owns the CA
  directory (for example ~/.local/share/todo-ca) and runs this tool:
      app_ca.py init --directory DIR --domain intern.example.org
      app_ca.py sign --directory DIR --request host.csr --output host.crt
  v1: root owns /var/lib/todo-ca (0700, ca.key 0600) and the Podman user can
      only run the narrow wrapper deploy/scripts/todo-ca-sign through sudo:
      a CSR on stdin, the certificate on stdout. The wrapper fixes the
      directory, the passphrase file, the validity and the extensions, so
      the caller chooses none of them (sign-stdin below).

Whoever runs it, the CA refuses anything it was not made for: the CSR's own
signature must verify; it may ask only for DNS names, each inside the
domains in the CA's X.509 name constraints, no wildcards; its key must be
RSA of at least 2048 bits or EC of at least 256; the certificate is at most
825 days (the most Apple clients accept), for TLS servers only, with the
subject and every extension set here, never copied from the CSR. And it
refuses a CA directory or key that others can read, or a passphrase file
kept next to the key.
"""
import argparse
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CA_DAYS = 7300
SERVER_DAYS = 365
# Apple platforms refuse TLS server certificates valid for longer, from any CA.
MAX_SERVER_DAYS = 825
# The v1 storage, owned by root; todo-ca-sign uses it.
V1_DIRECTORY = Path('/var/lib/todo-ca')
DOMAIN = re.compile(r'(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+')
# A request is a few kilobytes; anything much larger is not one.
MAX_REQUEST_BYTES = 64 * 1024
MAX_NAMES = 20
MIN_KEY_BITS = {'rsa': 2048, 'ec': 256}


class CaError(RuntimeError):
    """A request or a CA directory this tool refuses."""


def openssl(*arguments, passphrase=None, passphrase_file=None):
    """Run openssl and return its output.

    passphrase names the option for the CA key's passphrase (-pass or
    -passin). With passphrase_file it is read from that file; otherwise
    openssl asks on the terminal, so nothing is captured.
    """
    argv = ['openssl', *map(str, arguments)]
    capture = passphrase is None or passphrase_file is not None
    if passphrase and passphrase_file is not None:
        argv += [passphrase, f'file:{passphrase_file}']
    result = subprocess.run(argv, text=True, capture_output=capture, check=False,
                            stdin=subprocess.DEVNULL if capture else None)
    if result.returncode:
        detail = (result.stderr or '').strip().splitlines()[-1:] if capture else []
        raise CaError(f'openssl {arguments[0]} failed' + (f': {detail[0]}' if detail else ''))
    return result.stdout if capture else ''


def check_passphrase_file(directory, passphrase_file):
    """Refuse a passphrase file inside the CA directory: next to ca.key it would protect nothing."""
    if passphrase_file is None:
        return
    directory, path = Path(directory).resolve(), Path(passphrase_file).resolve()
    if path == directory or directory in path.parents:
        raise CaError(f'Keep the passphrase file out of {directory}: next to ca.key it protects nothing.')


def private(path, mode):
    """True if path is a regular file or directory (no symlink) of this user with exactly mode."""
    info = os.lstat(path)
    return (not stat.S_ISLNK(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == mode)


def check_storage(directory):
    """Refuse a CA directory that is not this user's alone: 0700, with ca.key 0600 and both owned by us.

    In development that user is the Podman user; in v1 it is root, and the
    Podman user cannot read the directory at all.
    """
    directory = Path(directory)
    ca_key, ca_certificate = directory / 'ca.key', directory / 'ca.crt'
    if not ca_key.exists() or not ca_certificate.exists():
        raise CaError(f'{directory} holds no CA; run init first.')
    if not private(directory, 0o700) or not private(ca_key, 0o600):
        raise CaError(f'{directory} must be 0700 and ca.key 0600, both owned by the user that signs '
                      f'(uid {os.geteuid()}); fix the permissions before signing.')
    if 'ENCRYPTED PRIVATE KEY' not in ca_key.read_text():
        raise CaError(f'{ca_key} is not encrypted; this CA keeps its key under a passphrase.')


def init(directory, domains, name, days=CA_DAYS, passphrase_file=None):
    """Create DIR/ca.key (encrypted), DIR/ca.crt and an empty DIR/issued.log, constrained to domains.

    Refuses an existing CA: a second one would make every client trust anew.
    """
    directory = Path(directory)
    domains = [domain.lower().strip('.') for domain in domains]
    for domain in domains:
        if not DOMAIN.fullmatch(domain):
            raise CaError(f'Not a DNS domain: {domain!r}')
    check_passphrase_file(directory, passphrase_file)
    if (directory / 'ca.key').exists() or (directory / 'ca.crt').exists():
        raise CaError(f'{directory} already holds a CA; a second one would make every client trust anew.')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    permitted = ','.join(f'permitted;DNS:{domain}' for domain in domains)
    openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-aes-256-cbc',
            '-out', directory / 'ca.key', passphrase='-pass', passphrase_file=passphrase_file)
    os.chmod(directory / 'ca.key', 0o600)
    openssl('req', '-x509', '-new', '-key', directory / 'ca.key', '-days', days, '-sha256',
            '-subj', f'/CN={name}',
            '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
            '-addext', 'subjectKeyIdentifier=hash',
            '-addext', f'nameConstraints=critical,{permitted}',
            '-out', directory / 'ca.crt', passphrase='-passin', passphrase_file=passphrase_file)
    os.chmod(directory / 'ca.crt', 0o644)
    os.close(os.open(directory / 'issued.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    return directory / 'ca.crt'


def permitted_domains(ca_certificate):
    """The DNS domains in the CA certificate's name constraints."""
    text = openssl('x509', '-in', ca_certificate, '-noout', '-ext', 'nameConstraints')
    return [line.strip()[4:].lower() for line in text.splitlines() if line.strip().startswith('DNS:')]


def requested_names(request):
    """The DNS names a CSR asks for; refuse a bad signature, a weak key or any other kind of name."""
    # openssl 3.0 exits 0 from req -verify even when the signature fails, so read what it says.
    result = subprocess.run(['openssl', 'req', '-in', str(request), '-noout', '-verify'], text=True,
                            capture_output=True, check=False, stdin=subprocess.DEVNULL)
    said = (result.stdout + result.stderr).lower()
    if result.returncode or 'failure' in said or not ('signature ok' in said or 'verify ok' in said):
        raise CaError(f'{request} is not a certificate signing request whose own signature verifies.')
    # -text, not -ext subjectAltName: openssl 3.0 req has no -ext.
    lines = openssl('req', '-in', request, '-noout', '-text').splitlines()
    marker = [index for index, line in enumerate(lines) if 'X509v3 Subject Alternative Name' in line]
    text = lines[marker[0] + 1] if marker and marker[0] + 1 < len(lines) else ''
    entries = [entry.strip() for entry in text.split(',') if entry.strip()]
    if not entries:
        raise CaError(f'{request} asks for no names (subjectAltName).')
    others = [entry for entry in entries if not entry.startswith('DNS:')]
    if others:
        raise CaError(f'{request} asks for names this CA does not issue: {", ".join(others)}')
    names = [entry[4:].lower() for entry in entries]
    if len(names) > MAX_NAMES:
        raise CaError(f'{request} asks for {len(names)} names; this CA issues at most {MAX_NAMES}.')
    invalid = [name for name in names if not DOMAIN.fullmatch(name)]
    if invalid:
        raise CaError(f'{request} asks for names that are not plain DNS names (no wildcards): {", ".join(invalid)}')
    check_key(lines, request)
    return names


def check_key(lines, request):
    """Refuse a request whose key is not RSA >= 2048 bits or EC >= 256 bits."""
    text = '\n'.join(lines)
    match = re.search(r'Public Key Algorithm: (\S+)', text)
    bits = re.search(r'Public-Key: \((\d+) bit\)', text)
    kind = 'rsa' if match and 'rsa' in match[1].lower() else 'ec' if match and 'ec' in match[1].lower() else None
    if kind is None or not bits or int(bits[1]) < MIN_KEY_BITS[kind]:
        raise CaError(f'{request} has a key this CA does not accept; use RSA of at least 2048 bits '
                      'or EC of at least 256 bits.')


def inside(name, domain):
    return name == domain or name.endswith('.' + domain)


def sign(directory, request, output, days=SERVER_DAYS, passphrase_file=None):
    """Sign request with the CA in directory into output; return the names it covers.

    Every check runs before the CA key is used. The subject is the first
    name, and every extension is set here: nothing in the CSR besides its
    public key and its DNS names reaches the certificate.
    """
    directory = Path(directory)
    check_storage(directory)
    check_passphrase_file(directory, passphrase_file)
    ca_key, ca_certificate = directory / 'ca.key', directory / 'ca.crt'
    if not 1 <= days <= MAX_SERVER_DAYS:
        raise CaError(f'--days must be between 1 and {MAX_SERVER_DAYS}.')
    names = requested_names(request)
    domains = permitted_domains(ca_certificate)
    outside = [name for name in names if not any(inside(name, domain) for domain in domains)]
    if outside:
        raise CaError(f'Outside this CA\'s domains ({", ".join(domains)}): {", ".join(outside)}')
    serial = secrets.token_hex(16)
    with tempfile.TemporaryDirectory() as temporary:
        extensions = Path(temporary) / 'server.ext'
        extensions.write_text('subjectAltName=' + ','.join(f'DNS:{name}' for name in names) + '\n'
                              'basicConstraints=critical,CA:FALSE\n'
                              'keyUsage=critical,digitalSignature,keyEncipherment\n'
                              'extendedKeyUsage=serverAuth\n'
                              'authorityKeyIdentifier=keyid\n'
                              'subjectKeyIdentifier=hash\n')
        openssl('x509', '-req', '-in', request, '-CA', ca_certificate, '-CAkey', ca_key,
                '-set_serial', '0x' + serial, '-days', days, '-sha256', '-subj', f'/CN={names[0]}',
                '-extfile', extensions, '-out', output, passphrase='-passin', passphrase_file=passphrase_file)
    for name in names:
        openssl('verify', '-CAfile', ca_certificate, '-purpose', 'sslserver', '-verify_hostname', name, output)
    expires = openssl('x509', '-in', output, '-noout', '-enddate').strip().split('=', 1)[1]
    log = os.open(directory / 'issued.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(log, 'a') as stream:
        stream.write(f'{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} serial={serial} '
                     f'expires="{expires}" names={",".join(names)} by_uid={os.getuid()}\n')
    return names


def sign_request(directory, request_text, passphrase_file=None):
    """sign-stdin: sign the CSR in request_text with SERVER_DAYS; return the certificate as text.

    The narrow form for todo-ca-sign under sudo: the caller hands over only
    the CSR and gets back only the certificate. No path the caller names is
    read or written, and the validity is fixed.
    """
    if len(request_text.encode()) > MAX_REQUEST_BYTES or '-----BEGIN CERTIFICATE REQUEST-----' not in request_text:
        raise CaError('stdin must hold one PEM certificate signing request.')
    with tempfile.TemporaryDirectory() as temporary:
        request, output = Path(temporary) / 'host.csr', Path(temporary) / 'host.crt'
        request.write_text(request_text)
        sign(directory, request, output, SERVER_DAYS, passphrase_file)
        return output.read_text()


def parser():
    result = argparse.ArgumentParser(description='CA for nginx certificates (provided TLS mode).')
    commands = result.add_subparsers(dest='command', required=True)
    create = commands.add_parser('init', help='create the root CA, once')
    create.add_argument('--directory', type=Path, required=True)
    create.add_argument('--domain', action='append', required=True,
                        help='a DNS domain the CA may issue for; repeat for more')
    create.add_argument('--name', default='Todo services root CA')
    create.add_argument('--days', type=int, default=CA_DAYS)
    issue = commands.add_parser('sign', help='sign a request file into a certificate file (development)')
    issue.add_argument('--directory', type=Path, required=True)
    issue.add_argument('--request', type=Path, required=True)
    issue.add_argument('--output', type=Path, required=True)
    issue.add_argument('--days', type=int, default=SERVER_DAYS)
    narrow = commands.add_parser('sign-stdin', help='a CSR on stdin, the certificate on stdout (todo-ca-sign)')
    narrow.add_argument('--directory', type=Path, required=True)
    for command in (create, issue, narrow):
        # Not next to ca.key (refused). Without it, openssl asks on the terminal.
        command.add_argument('--passphrase-file', type=Path,
                             help='file holding the CA key passphrase, outside the CA directory')
    return result


def main(arguments=None):
    args = parser().parse_args(arguments)
    try:
        if args.command == 'init':
            certificate = init(args.directory, args.domain, args.name, args.days, args.passphrase_file)
            print(f'Created {certificate}. Every client trusts this file once; ca.key never leaves '
                  f'{args.directory}.')
        elif args.command == 'sign':
            names = sign(args.directory, args.request, args.output, args.days, args.passphrase_file)
            print(f'Signed {args.output} for {", ".join(names)}. Then: python3 -m app_installer '
                  f'tls-install --certificate {args.output} --ca {args.directory / "ca.crt"}')
        else:
            sys.stdout.write(sign_request(args.directory, sys.stdin.read(MAX_REQUEST_BYTES + 1),
                                          args.passphrase_file))
    except (CaError, OSError) as error:
        print(f'app_ca: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
