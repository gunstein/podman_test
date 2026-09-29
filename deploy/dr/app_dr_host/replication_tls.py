"""TLS for the replication stream between the two hosts; nothing about roles or copying.

The WAL stream carries every row of all three databases, so it must not cross
a network between sites in clear text. This module only encrypts it:

- ensure_ca / make_ca: one replication CA, kept as Podman secrets on both
  hosts. Both hold the CA key, because either can become the primary that
  has to issue its own certificate.
- issue / certificate_ok: a server certificate for the primary's own
  address, signed by that CA, and the check that it still fits.
- install_server_tls: put the certificate into the PostgreSQL container, turn
  on ssl (TLS 1.2 at least) and reload. A certificate with less than 30 days
  left is issued again.

replication.py decides when: configure_primary and publish_primaries call
ensure_ca and install_server_tls; the standby only uses the CA certificate
with sslmode=verify-full, so it refuses any server without a certificate for
the primary's address from this CA. The certificate lasts 825 days and is
renewed only when a primary is published (bootstrap or rebuild); see backlog
U2. This CA is not the nginx CA that users trust (backlog T4).

The host's openssl command does the certificate work; the Python standard
library cannot create certificates. Private keys only pass through a 0700
temporary directory and the database containers.
"""
import secrets as random
import tempfile
import time
from pathlib import Path

from app_installer import apps, secrets
from app_installer.commands import exists, run

DATA = '/var/lib/postgresql/data'
# The standby's copy of the CA certificate, next to its passfile.
STANDBY_CA_FILE = f'{DATA}/replication-ca.crt'
CA_DAYS = 3650
SERVER_DAYS = 825
RENEW_SECONDS = 30 * 24 * 3600


def openssl(*arguments, allowed=(0,)):
    """Run the host's openssl; commands.run names it if it is missing."""
    return run('openssl', *arguments, allowed=allowed)


def make_ca(directory):
    """Create a CA key and a self-signed CA certificate in directory; return their paths."""
    key, certificate = Path(directory) / 'ca.key', Path(directory) / 'ca.crt'
    openssl('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
            '-days', str(CA_DAYS), '-subj', '/CN=todo replication CA',
            '-addext', 'basicConstraints=critical,CA:TRUE',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
            '-keyout', str(key), '-out', str(certificate))
    return key, certificate


def issue(directory, ca_key, ca_certificate, node_address):
    """Issue a server key and certificate for node_address; return their paths.

    The certificate names the host's IPv4 address, which the standby checks
    with verify-full, and the database container names, which the primary's
    own replication login over app-network uses.
    """
    directory = Path(directory)
    key, request, certificate = directory / 'server.key', directory / 'server.csr', directory / 'server.crt'
    names = ','.join([f'IP:{node_address}'] + [f'DNS:{database.container}'
                                                 for database in apps.REPLICATED_DATABASES])
    extensions = directory / 'server.ext'
    extensions.write_text(f'subjectAltName={names}\nbasicConstraints=critical,CA:FALSE\n'
                          'keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n')
    openssl('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
            '-subj', f'/CN={node_address}', '-keyout', str(key), '-out', str(request))
    openssl('x509', '-req', '-in', str(request), '-CA', str(ca_certificate), '-CAkey', str(ca_key),
            '-set_serial', '0x' + random.token_hex(16), '-days', str(SERVER_DAYS),
            '-extfile', str(extensions), '-out', str(certificate))
    return key, certificate


def certificate_ok(certificate, ca_certificate, node_address):
    """True if the certificate is signed by the CA, names node_address and is not about to expire."""
    certificate, ca_certificate = str(certificate), str(ca_certificate)
    if openssl('verify', '-CAfile', ca_certificate, certificate, allowed=(0, 2)).returncode:
        return False
    if openssl('x509', '-in', certificate, '-noout', '-checkend', str(RENEW_SECONDS),
               allowed=(0, 1)).returncode:
        return False
    names = openssl('x509', '-in', certificate, '-noout', '-ext', 'subjectAltName').stdout
    return f'IP Address:{node_address}' in [part.strip() for part in names.replace(',', '\n').splitlines()]


def ensure_ca():
    """Create the replication CA secrets on this host if they are missing; True if created."""
    key_secret, certificate_secret = apps.REPLICATION_CA_SECRETS
    present = [exists('secret', name) for name in apps.REPLICATION_CA_SECRETS]
    if all(present):
        return False
    if any(present):
        raise RuntimeError('Only half of the replication CA exists; resolve it explicitly.')
    with tempfile.TemporaryDirectory() as directory:
        key, certificate = make_ca(directory)
        run('podman', 'secret', 'create', key_secret, str(key))
        run('podman', 'secret', 'create', certificate_secret, str(certificate))
    return True


def install_server_tls(database, node_address):
    """Give this primary database a valid host certificate and turn TLS on; True if anything changed.

    Keeps a certificate that the CA signed for node_address and that is not
    about to expire; otherwise issues a new one into the data directory. Then
    turns ssl on (TLS 1.2 or newer) and reloads, which needs no restart, and
    waits until the server reports ssl on. A primary copied from another host
    (after promotion) gets a certificate for its own address here.
    """
    container = database.container
    changed = False
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        ca_key, ca_certificate = directory / 'ca.key', directory / 'ca.crt'
        key_secret, certificate_secret = apps.REPLICATION_CA_SECRETS
        ca_key.touch(mode=0o600)
        ca_key.write_text(secrets.read(key_secret) + '\n')
        ca_certificate.write_text(secrets.read(certificate_secret) + '\n')
        current = directory / 'current.crt'
        current.write_text(run('podman', 'exec', container, 'cat', f'{DATA}/server.crt',
                               allowed=(0, 1)).stdout)
        if not current.read_text().strip() or not certificate_ok(current, ca_certificate, node_address):
            key, certificate = issue(directory, ca_key, ca_certificate, node_address)
            for source, target in ((key, 'server.key'), (certificate, 'server.crt')):
                # stdin holds the private key: never show this command's output in an error.
                run('podman', 'exec', '-i', container, 'sh', '-c',
                    f'umask 077 && cat > {DATA}/{target}.new && mv {DATA}/{target}.new {DATA}/{target}',
                    input=source.read_text(), secret_output=True)
            changed = True
    from .replication import sql
    if sql(database, "SELECT current_setting('ssl'), current_setting('ssl_min_protocol_version');") != 'on|TLSv1.2':
        sql(database, "ALTER SYSTEM SET ssl = 'on';")
        sql(database, "ALTER SYSTEM SET ssl_min_protocol_version = 'TLSv1.2';")
        changed = True
    if changed:
        sql(database, 'SELECT pg_reload_conf();')
        for _ in range(20):
            if sql(database, "SELECT current_setting('ssl');") == 'on':
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f'{database.name}: PostgreSQL did not turn TLS on; check its log for the certificate')
    return changed
