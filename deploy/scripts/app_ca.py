#!/usr/bin/env python3
"""A small offline CA for nginx's certificates (provided TLS mode, docs/TLS.md).

Runs on an administrator's machine that is not one of the servers, with
Python 3 and openssl only. The CA directory belongs on encrypted storage that
is offline between uses (an encrypted USB stick, with a second copy kept
elsewhere); the CA key in it is encrypted with a passphrase openssl asks for.

  app_ca.py init --directory DIR --domain intern.example.org [--domain ...]
      once: create the root CA (20 years). Its name constraints allow only
      names in the given domains, so even a stolen CA key cannot issue a
      certificate for anyone else's site.
  app_ca.py sign --directory DIR --request host.csr --output host.crt [--days 365]
      for each host, about once a year: sign the request a host made with
      `app_installer tls-request`. Only DNS names inside the CA's domains,
      at most 825 days (the most Apple clients accept). Every certificate
      issued is appended to DIR/issued.log.

The host then installs it with
  python3 -m app_installer tls-install --certificate host.crt --ca DIR/ca.crt
and every client trusts DIR/ca.crt once. The CA never sees a host's
private key: the request holds only the public key.
"""
import argparse
import os
import re
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CA_DAYS = 7300
SERVER_DAYS = 365
# Apple platforms refuse TLS server certificates valid for longer, from any CA.
MAX_SERVER_DAYS = 825
DOMAIN = re.compile(r'(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+')


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
    result = subprocess.run(argv, text=True, capture_output=capture, check=False)
    if result.returncode:
        detail = (result.stderr or '').strip().splitlines()[-1:] if capture else []
        raise CaError(f'openssl {arguments[0]} failed' + (f': {detail[0]}' if detail else ''))
    return result.stdout if capture else ''


def init(directory, domains, name, days=CA_DAYS, passphrase_file=None):
    """Create DIR/ca.key (encrypted) and DIR/ca.crt, constrained to domains; refuse an existing CA."""
    directory = Path(directory)
    domains = [domain.lower().strip('.') for domain in domains]
    for domain in domains:
        if not DOMAIN.fullmatch(domain):
            raise CaError(f'Not a DNS domain: {domain!r}')
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
    return directory / 'ca.crt'


def permitted_domains(ca_certificate):
    """The DNS domains in the CA certificate's name constraints."""
    text = openssl('x509', '-in', ca_certificate, '-noout', '-ext', 'nameConstraints')
    return [line.strip()[4:].lower() for line in text.splitlines() if line.strip().startswith('DNS:')]


def requested_names(request):
    """The DNS names a CSR asks for; refuse a bad signature or any other kind of name."""
    openssl('req', '-in', request, '-noout', '-verify')
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
    return [entry[4:].lower() for entry in entries]


def inside(name, domain):
    return name == domain or name.endswith('.' + domain)


def sign(directory, request, output, days=SERVER_DAYS, passphrase_file=None):
    """Sign request with the CA in directory into output; return the names it covers."""
    directory = Path(directory)
    ca_key, ca_certificate = directory / 'ca.key', directory / 'ca.crt'
    if not ca_key.exists() or not ca_certificate.exists():
        raise CaError(f'{directory} holds no CA; run init first.')
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
                '-set_serial', '0x' + serial, '-days', days, '-sha256', '-extfile', extensions,
                '-out', output, passphrase='-passin', passphrase_file=passphrase_file)
    for name in names:
        openssl('verify', '-CAfile', ca_certificate, '-purpose', 'sslserver', '-verify_hostname', name, output)
    expires = openssl('x509', '-in', output, '-noout', '-enddate').strip().split('=', 1)[1]
    with open(directory / 'issued.log', 'a') as log:
        log.write(f'{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} serial={serial} '
                  f'expires="{expires}" names={",".join(names)}\n')
    return names


def parser():
    result = argparse.ArgumentParser(description='Offline CA for nginx certificates (provided TLS mode).')
    commands = result.add_subparsers(dest='command', required=True)
    create = commands.add_parser('init', help='create the root CA, once')
    create.add_argument('--directory', type=Path, required=True)
    create.add_argument('--domain', action='append', required=True,
                        help='a DNS domain the CA may issue for; repeat for more')
    create.add_argument('--name', default='Todo services root CA')
    create.add_argument('--days', type=int, default=CA_DAYS)
    issue = commands.add_parser('sign', help="sign a host's request")
    issue.add_argument('--directory', type=Path, required=True)
    issue.add_argument('--request', type=Path, required=True)
    issue.add_argument('--output', type=Path, required=True)
    issue.add_argument('--days', type=int, default=SERVER_DAYS)
    for command in (create, issue):
        # For tests and scripted labs; by default openssl asks on the terminal.
        command.add_argument('--passphrase-file', type=Path, help=argparse.SUPPRESS)
    return result


def main(arguments=None):
    args = parser().parse_args(arguments)
    try:
        if args.command == 'init':
            certificate = init(args.directory, args.domain, args.name, args.days, args.passphrase_file)
            print(f'Created {certificate}. Every client trusts this file once; keep {args.directory} offline.')
        else:
            names = sign(args.directory, args.request, args.output, args.days, args.passphrase_file)
            print(f'Signed {args.output} for {", ".join(names)}. On the host: python3 -m app_installer '
                  f'tls-install --certificate {args.output.name} --ca ca.crt')
    except (CaError, OSError) as error:
        print(f'app_ca: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
