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
- renew: the same for a running primary, for the address its certificate
  already names; days_left and ca_days_left say how long the certificate
  and the CA still last.

replication.py decides when a primary gets its first certificate:
configure_primary and publish_primaries call ensure_ca and
install_server_tls. The standby only uses the CA certificate with
sslmode=verify-full, so it refuses any server without a certificate for the
primary's address from this CA. The certificate lasts 825 days.
platform-replication-tls.timer runs `app_dr.py renew-tls` every night on both
hosts: on the primary it calls renew, which issues a new certificate once
fewer than 30 days are left, and PostgreSQL reloads it without a restart;
the standby has nothing to renew. `app_dr.py check` fails when a
certificate has fewer than ALERT_DAYS left, so a renewal that keeps failing
is seen in time. The CA lasts 10 years and is not renewed automatically
(backlog U2). This CA is not the nginx CA that users trust (backlog T4).

The host's openssl command does the certificate work; the Python standard
library cannot create certificates. Private keys only pass through a 0700
temporary directory and the database containers.
"""
import secrets as random
import tempfile
import time
from pathlib import Path

from app_installer import apps, secrets, tls
from app_installer.commands import exists, run

DATA = '/var/lib/postgresql/data'
CA_DAYS = 3650
SERVER_DAYS = 825
RENEW_SECONDS = 30 * 24 * 3600
# app_dr.py check fails below these. The nightly renewal replaces a server
# certificate at 30 days, so 25 means it has failed for about five nights.
# The CA is replaced by hand, which needs months of notice.
ALERT_DAYS = 25
CA_ALERT_DAYS = 180


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


def days_left(certificate):
    """Whole days until the certificate file expires; negative once it has expired."""
    return tls.days_until(openssl('x509', '-in', str(certificate), '-noout', '-enddate').stdout)


def certificate_address(certificate):
    """The IPv4 address in the certificate's subjectAltName, or '' if it names none."""
    names = openssl('x509', '-in', str(certificate), '-noout', '-ext', 'subjectAltName').stdout
    for part in names.replace(',', '\n').splitlines():
        if part.strip().startswith('IP Address:'):
            return part.strip().split(':', 1)[1]
    return ''


def current_certificate(database, directory):
    """Copy the primary's server certificate to directory/current.crt and return its path; raise if it has none."""
    text = run('podman', 'exec', database.container, 'cat', f'{DATA}/server.crt', allowed=(0, 1)).stdout
    if not text.strip():
        raise RuntimeError(f'{database.name}: the primary has no replication certificate; '
                           'publish it with app-ops (bootstrap or rebuild) first')
    path = Path(directory) / 'current.crt'
    path.write_text(text)
    return path


def server_days_left(database):
    """Whole days the primary's replication certificate still lasts."""
    with tempfile.TemporaryDirectory() as directory:
        return days_left(current_certificate(database, directory))


def ca_days_left():
    """Whole days the replication CA certificate on this host still lasts."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'ca.crt'
        path.write_text(secrets.read(apps.REPLICATION_CA_SECRETS[1]) + '\n')
        return days_left(path)


def renew(database):
    """On a running primary, replace a certificate that is about to expire; True if it was replaced.

    The new certificate names the same address as the current one, so the
    standby, which connects to that address with verify-full, accepts it.
    A certificate that still has more than 30 days, from this host's CA, is
    kept and nothing changes. A new one is installed by install_server_tls,
    which reloads PostgreSQL: the standby's running stream is not cut, and
    its next connection gets the new certificate. A primary without a
    certificate, or with one that names no address, is left to app-ops: it
    was never published, and renewal must not guess its address. After a
    failover the promoted primary still holds the old primary's certificate
    (pg_basebackup copied it) until rebuild-standby publishes it for its own
    address; renewing it before then is harmless, as no standby connects.
    """
    with tempfile.TemporaryDirectory() as directory:
        current = current_certificate(database, directory)
        node_address = certificate_address(current)
        if not node_address:
            raise RuntimeError(f'{database.name}: the replication certificate names no address; '
                               'publish the primary again with app-ops')
        ca_certificate = Path(directory) / 'ca.crt'
        ca_certificate.write_text(secrets.read(apps.REPLICATION_CA_SECRETS[1]) + '\n')
        if certificate_ok(current, ca_certificate, node_address):
            return False
    install_server_tls(database, node_address)
    return True
