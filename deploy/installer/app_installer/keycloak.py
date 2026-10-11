"""Local nginx readiness and Keycloak administration using the standard library."""
import copy
import json
import re
import time
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from . import apps, settings

BASE = f'http://127.0.0.1:{settings.LOCAL_HTTP_PORT}'

# Where Keycloak's OIDC addresses are: every other module asks here, so a
# different provider changes this place (docs/IDENTITY.md). Keycloak serves
# under /auth/ (KC_HTTP_RELATIVE_PATH), nginx routes /auth/ to it, and the
# platform's apps share one realm, todo, until phase 6.
REALM = 'todo'
REALM_PATH = f'/auth/realms/{REALM}'
# The discovery document, relative to an origin: the browser asks through
# its app's origin, the installer, DR and backup through the local nginx.
DISCOVERY = REALM_PATH + '/.well-known/openid-configuration'
# The signing keys, as a backend fetches them inside app-network (not the
# public issuer's, which the backend could not reach by its public name).
JWKS_URL = f'http://keycloak:8080{REALM_PATH}/protocol/openid-connect/certs'
# Keycloak's own administration, not OIDC: the admin's token from the master
# realm, and the realm's admin API (its settings and clients).
ADMIN_TOKEN = '/auth/realms/master/protocol/openid-connect/token'
ADMIN_REALM = f'/auth/admin/realms/{REALM}'


def issuer(hostname, port):
    """The issuer every token names: Keycloak's public hostname and HTTPS port, and the realm."""
    return f'https://{hostname}:{port}{REALM_PATH}'
# Login protection for the todo realm. After 5 failed logins an account is
# locked for a minute, doubling up to 15 minutes; failures are forgotten after
# 12 hours. Keycloak leaves both off by default. The realm import carries the
# same values for a new installation (keycloak/todo-realm.json).
REALM_SECURITY = {
    'bruteForceProtected': True,
    'permanentLockout': False,
    'failureFactor': 5,
    'waitIncrementSeconds': 60,
    'maxFailureWaitSeconds': 900,
    'maxDeltaTimeSeconds': 43200,
    'quickLoginCheckMilliSeconds': 1000,
    'minimumQuickLoginWaitSeconds': 60,
    'passwordPolicy': 'length(12) and notUsername and notEmail',
}


def request(path, method='GET', data=None, token=None, form=False, hostname=None):
    """Send one HTTP request to the local nginx on 127.0.0.1 and return the JSON reply.

    hostname sets the Host header, which picks the app's virtual host.
    Raises unless the status is the one Keycloak's admin API returns on
    success: 204 for PUT, 201 for creating a client, else 200. Those two
    have no body, so they return an empty dict.
    """
    headers = {'Host': hostname} if hostname else {}
    body = None
    if data is not None:
        body = (urlencode(data) if form else json.dumps(data)).encode()
        headers['Content-Type'] = ('application/x-www-form-urlencoded' if form
                                   else 'application/json')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    with urlopen(Request(BASE + path, data=body, headers=headers, method=method),
                 timeout=30) as response:
        expected = (204 if method == 'PUT' else
                    201 if method == 'POST' and path.endswith('/clients') else 200)
        if response.status != expected:
            raise RuntimeError(f'Unexpected HTTP status {response.status} for {path}')
        return json.load(response) if response.status == 200 else {}


def wait(path, attempts, delay, status=None, hostname=None):
    """Poll path until it answers, and until its "status" equals status if given.

    Tries attempts times, delay seconds apart, then raises. Used while
    services start, when refused connections are expected.
    """
    for attempt in range(attempts):
        try:
            data = request(path, hostname=hostname) if hostname else request(path)
            if status is None or data.get('status') == status:
                return data
        except (URLError, TimeoutError, ConnectionError, ValueError, RuntimeError):
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise RuntimeError(f'Readiness failed after {attempts} attempts: {hostname or BASE}{path}')


def secure_realm(token):
    """Give the todo realm REALM_SECURITY, also on an existing installation.

    A realm is imported only on Keycloak's first start, so an installation
    made before these settings, or a replicated Keycloak database, gets them
    here. Returns True if the realm had to be changed.
    """
    realm = request(ADMIN_REALM, token=token)
    if all(realm.get(key) == value for key, value in REALM_SECURITY.items()):
        return False
    request(ADMIN_REALM, 'PUT', dict(REALM_SECURITY), token)
    return True


def template_client(token):
    """The client the realm import brought (apps.TEMPLATE_CLIENT), which a missing client is copied from."""
    matches = request(ADMIN_REALM + '/clients?' + urlencode({'clientId': apps.TEMPLATE_CLIENT}),
                      token=token)
    if len(matches) != 1:
        raise RuntimeError(f'The realm import client {apps.TEMPLATE_CLIENT} must exist before adding clients.')
    return request(ADMIN_REALM + '/clients/' + matches[0]['id'], token=token)


def configure(admin_password, clients):
    """Make sure every app has a Keycloak client whose redirect and origin match its URL.

    Waits for nginx and Keycloak (its hostname is nginx's default server),
    never for the apps (their checks run after this, checks.py), checks that
    the issuer is HTTPS with the todo realm, then logs in as the Keycloak admin. The realm import brings one client,
    apps.TEMPLATE_CLIENT: another app's missing client is copied from it,
    with its own client ID, redirect URL and token audience. An existing
    client only gets its URLs corrected. The realm gets its login
    protection first (secure_realm). Returns True if anything changed.
    """
    discovery = wait(DISCOVERY, 90, 2)
    found = discovery.get('issuer', '')
    if not re.fullmatch(r'https://[^/]+' + re.escape(REALM_PATH), found):
        raise RuntimeError(f'Expected an HTTPS issuer with the realm path {REALM_PATH}, not {found!r}.')
    identities = list(clients)  # [(client ID, the hostname its app is served on)], install.clients()
    token = request(ADMIN_TOKEN, 'POST', {
        'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin',
        'password': admin_password,
    }, form=True)['access_token']
    parsed = urlsplit(found)
    changed = secure_realm(token)
    for client_id, hostname in identities:
        # Each app's client: its own public hostname on the issuer's port.
        client_origin = f'https://{hostname}' + (f':{parsed.port}' if parsed.port else '')
        matches = request(ADMIN_REALM + '/clients?' + urlencode({'clientId': client_id}),
                          token=token)
        if len(matches) > 1:
            raise RuntimeError(f'Expected at most one Keycloak client named {client_id}.')
        if not matches:
            template = template_client(token)
            client = copy.deepcopy({key: value for key, value in template.items() if key in (
                'publicClient', 'protocol', 'standardFlowEnabled', 'directAccessGrantsEnabled',
                'serviceAccountsEnabled', 'attributes', 'defaultClientScopes',
                'optionalClientScopes', 'protocolMappers')})
            client.update(clientId=client_id, name=client_id, enabled=True,
                          redirectUris=[client_origin + '/'], webOrigins=[client_origin])
            for mapper in client.get('protocolMappers', []):
                mapper.pop('id', None)
                config = mapper.get('config', {})
                if config.get('included.client.audience') == apps.TEMPLATE_CLIENT:
                    config['included.client.audience'] = client_id
            request(ADMIN_REALM + '/clients', 'POST', client, token)
            changed = True
            continue
        path = ADMIN_REALM + '/clients/' + matches[0]['id']
        client = request(path, token=token)
        if client.get('redirectUris') == [client_origin + '/'] and client.get('webOrigins') == [client_origin]:
            continue
        client.update(redirectUris=[client_origin + '/'], webOrigins=[client_origin])
        request(path, 'PUT', client, token)
        changed = True
    return changed
