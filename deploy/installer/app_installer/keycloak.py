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


def request(path, method='GET', data=None, token=None, form=False, hostname=None):
    """Send one HTTP request to the local nginx on 127.0.0.1 and return the JSON reply.

    hostname sets the Host header, which picks the app's virtual host.
    Raises unless the status is the one Keycloak's admin API returns on
    success: 204 for PUT, 201 for creating a client, else 200.
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
        return json.load(response) if response.status == 200 else None


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


def configure(admin_password, clients=None):
    """Make sure every app has a Keycloak client whose redirect and origin match its URL.

    Waits for nginx, the apps and Keycloak, checks that the issuer is HTTPS
    with the todo realm, then logs in as the Keycloak admin. The first app's
    client comes with the realm import and serves as the template: another
    app's missing client is copied from it, with its own client ID,
    redirect URL and token audience. An existing client only gets its URLs
    corrected. Returns True if anything changed.
    """
    wait('/health', 30, 1, 'ok')
    wait('/ready', 30, 1, 'ready')
    discovery = wait('/auth/realms/todo/.well-known/openid-configuration', 90, 2)
    issuer = discovery.get('issuer', '')
    if not re.fullmatch(r'https://[^/]+/auth/realms/todo', issuer):
        raise RuntimeError('Expected an HTTPS issuer with the Todo realm path.')
    origin = issuer.removesuffix('/auth/realms/todo')
    identities = ([(app.keycloak_client, app.hostname) for app in apps.APPS]
                  if clients is None else list(clients))
    for client_id, hostname in identities:
        if client_id != apps.SHARED_RESOURCE_OWNER.keycloak_client:
            wait('/health', 30, 1, 'ok', hostname=hostname)
            wait('/ready', 30, 1, 'ready', hostname=hostname)
    token = request('/auth/realms/master/protocol/openid-connect/token', 'POST', {
        'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin',
        'password': admin_password,
    }, form=True)['access_token']
    parsed = urlsplit(issuer)
    changed = False
    template = None
    for client_id, hostname in identities:
        # The shared-resource-owner app follows the environment's canonical issuer
        # (localhost in the original local profile); other apps use their registry hostnames.
        client_origin = (origin if client_id == apps.SHARED_RESOURCE_OWNER.keycloak_client
                         else f'https://{hostname}' + (f':{parsed.port}' if parsed.port else ''))
        matches = request('/auth/admin/realms/todo/clients?' + urlencode({'clientId': client_id}),
                          token=token)
        if len(matches) > 1:
            raise RuntimeError(f'Expected at most one Keycloak client named {client_id}.')
        if not matches:
            if template is None:
                raise RuntimeError('The identity application client must exist before adding clients.')
            client = copy.deepcopy({key: value for key, value in template.items() if key in (
                'publicClient', 'protocol', 'standardFlowEnabled', 'directAccessGrantsEnabled',
                'serviceAccountsEnabled', 'attributes', 'defaultClientScopes',
                'optionalClientScopes', 'protocolMappers')})
            client.update(clientId=client_id, name=client_id, enabled=True,
                          redirectUris=[client_origin + '/'], webOrigins=[client_origin])
            for mapper in client.get('protocolMappers', []):
                mapper.pop('id', None)
                config = mapper.get('config', {})
                if config.get('included.client.audience') == apps.SHARED_RESOURCE_OWNER.keycloak_client:
                    config['included.client.audience'] = client_id
            request('/auth/admin/realms/todo/clients', 'POST', client, token)
            changed = True
            continue
        path = '/auth/admin/realms/todo/clients/' + matches[0]['id']
        client = request(path, token=token)
        if client_id == apps.SHARED_RESOURCE_OWNER.keycloak_client:
            template = client
        if client.get('redirectUris') == [client_origin + '/'] and client.get('webOrigins') == [client_origin]:
            continue
        client.update(redirectUris=[client_origin + '/'], webOrigins=[client_origin])
        request(path, 'PUT', client, token)
        changed = True
    return changed
