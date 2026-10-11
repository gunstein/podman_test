"""CI: a Keycloak client broken by hand is put right by the next install's client setup.

The full-stack job runs it after the stack is up and before the browser tests, as
PYTHONPATH=deploy/installer python deploy/scripts/dev/check_client_repair.py.
It breaks Todo's client in the real Keycloak where an operator could (PKCE off,
direct password grants on, a second audience mapper that also names a client
audience), runs the client setup the install runs (install.configure_identity),
and checks the client again; the browser tests that follow then log in and check
each token's audience. It lives in a .py file, not in the workflow, so ruff and
pyright check its calls.
"""
from urllib.parse import urlencode

from app_installer import apps, install, keycloak, platform_file, secrets


def admin_token():
    password = secrets.read(apps.KEYCLOAK_ADMIN_SECRET)
    return keycloak.request(keycloak.ADMIN_TOKEN, 'POST', {
        'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin', 'password': password,
    }, form=True)['access_token']


def client_path(token, client_id):
    [found] = keycloak.request(keycloak.ADMIN_REALM + '/clients?' + urlencode({'clientId': client_id}), token=token)
    return keycloak.ADMIN_REALM + '/clients/' + found['id']


def main():
    platform, _environment = platform_file.load(platform_file.CHECKOUT_FILE, 'local')
    app = platform.login_apps[0]
    token = admin_token()
    path = client_path(token, app.login_client)

    # Break it as an operator could.
    client = keycloak.request(path, token=token)
    client.pop('protocolMappers', None)
    client.update(directAccessGrantsEnabled=True, attributes={**client.get('attributes', {}),
                                                              'pkce.code.challenge.method': ''})
    keycloak.request(path, 'PUT', client, token)
    both = keycloak.audience_mapper(app.login_audience)
    both['name'] = 'audience naming the client too'
    both['config'] = {**both['config'], 'included.client.audience': app.login_client}
    keycloak.request(path + '/protocol-mappers/models', 'POST', both, token)

    # The install's client setup puts it right.
    if not install.configure_identity(platform, {each.name: each.hostname for each in platform.apps}):
        raise SystemExit('the client setup changed nothing on a broken client')
    client = keycloak.request(path, token=admin_token())
    if client.get('directAccessGrantsEnabled') or \
            client.get('attributes', {}).get('pkce.code.challenge.method') != 'S256':
        raise SystemExit(f'{app.login_client}: grants or PKCE not corrected')
    audiences = [mapper for mapper in keycloak.request(path + '/protocol-mappers/models', token=admin_token())
                 if mapper.get('protocolMapper') == 'oidc-audience-mapper']
    if len(audiences) != 1 or not keycloak.audience_mapper_is(audiences[0], keycloak.audience_mapper(app.login_audience)):
        raise SystemExit(f'{app.login_client}: audience mappers not corrected: {audiences}')
    # Running it again changes nothing.
    if install.configure_identity(platform, {each.name: each.hostname for each in platform.apps}):
        raise SystemExit('a second client setup changed something')
    print(f'{app.login_client}: a broken client was put right, and a second setup changed nothing')


if __name__ == '__main__':
    main()
