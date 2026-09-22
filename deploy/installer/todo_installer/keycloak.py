"""Local nginx readiness and Keycloak administration using the standard library."""
import json
import re
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:8080'


def request(path, method='GET', data=None, token=None, form=False):
    headers = {}
    body = None
    if data is not None:
        body = (urlencode(data) if form else json.dumps(data)).encode()
        headers['Content-Type'] = ('application/x-www-form-urlencoded' if form
                                   else 'application/json')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    with urlopen(Request(BASE + path, data=body, headers=headers, method=method),
                 timeout=30) as response:
        expected = 204 if method == 'PUT' else 200
        if response.status != expected:
            raise RuntimeError(f'Unexpected HTTP status {response.status} for {path}')
        return json.load(response) if response.status != 204 else None


def wait(path, attempts, delay, status=None):
    for attempt in range(attempts):
        try:
            data = request(path)
            if status is None or data.get('status') == status:
                return data
        except (URLError, TimeoutError, ConnectionError, ValueError, RuntimeError):
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise RuntimeError(f'Readiness failed after {attempts} attempts: {path}')


def configure(admin_password):
    wait('/health', 30, 1, 'ok')
    wait('/ready', 30, 1, 'ready')
    discovery = wait('/auth/realms/todo/.well-known/openid-configuration', 90, 2)
    issuer = discovery.get('issuer', '')
    if not re.fullmatch(r'https://[^/]+/auth/realms/todo', issuer):
        raise RuntimeError('Expected an HTTPS issuer with the Todo realm path.')
    origin = issuer.removesuffix('/auth/realms/todo')
    token = request('/auth/realms/master/protocol/openid-connect/token', 'POST', {
        'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin',
        'password': admin_password,
    }, form=True)['access_token']
    clients = request('/auth/admin/realms/todo/clients?clientId=todo-frontend', token=token)
    if len(clients) != 1:
        raise RuntimeError('Expected exactly one Keycloak client named todo-frontend.')
    path = '/auth/admin/realms/todo/clients/' + clients[0]['id']
    client = request(path, token=token)
    if client.get('redirectUris') == [origin + '/'] and client.get('webOrigins') == [origin]:
        return False
    client.update(redirectUris=[origin + '/'], webOrigins=[origin])
    request(path, 'PUT', client, token)
    return True
