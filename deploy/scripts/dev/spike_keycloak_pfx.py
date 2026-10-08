"""CI spike for BACKLOG X1 step 2: can Keycloak sign tokens with a key from a PFX file secret?

Run on a host with rootless Podman and openssl (CI: the "Keycloak PFX spike"
job). Nothing here belongs to the installer yet; it answers the questions the
installer work depends on, with the same Podman mechanics:

  1. A PKCS#12 file (.pfx) under a random password, from a demo CA, made with
     openssl, as an organisation would deliver it.
  2. The PFX as a Kube secret built from the file's bytes (base64), mounted as
     a file by podman kube play, in the realm's directory where Keycloak
     requires it: does it arrive byte for byte, and can Keycloak's user
     (1000, group 0) read it?
  3. Keycloak's java-keystore key provider, through the admin API: with the
     password from Keycloak's file vault (a second file secret, so the
     password never crosses the API), else as a plain value.
  4. A token from the realm is signed with the PFX key: its kid is that key,
     the JWKS publishes the PFX certificate (x5c), and openssl verifies the
     signature with the certificate's public key.
  5. A wrong password and a missing file are refused when the key is added.

It prints one RESULT line per question and exits 1 if a required one fails.
Everything it creates (pod, secrets, temporary files) is removed at the end.
"""
import base64
import json
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
POD = 'keycloak-pfx-spike'
CONTAINER = f'{POD}-keycloak'
PORT = 18080
BASE = f'http://127.0.0.1:{PORT}'
REALM = 'spike'
ALIAS = 'todo-signing'
PFX_SECRET = 'keycloak-pfx-spike-signing'
VAULT_SECRET = 'keycloak-pfx-spike-vault'
ADMIN_SECRET = 'keycloak-pfx-spike-admin'
# Keycloak 26 loads a realm's keystore only from that realm's directory under
# /opt/keycloak/data (first CI run: "is not under the realm directory").
SIGNING_DIR = f'/opt/keycloak/data/{REALM}'
VAULT_DIR = '/opt/keycloak/vault'
# The file vault's name for ${vault.signing-password} in this realm: <realm>_<key>.
VAULT_KEY = 'signing-password'


def run(*argv, input=None, check=True):
    """Run a command; return its stdout as bytes."""
    result = subprocess.run(argv, input=input, capture_output=True, check=False)
    if check and result.returncode:
        raise RuntimeError(f'{argv[0]} {argv[1]} failed ({result.returncode}): '
                           + result.stderr.decode(errors='replace').strip()[-2000:])
    return result.stdout


def keycloak_image():
    """The upstream image keycloak/Containerfile builds on, so the spike tracks the stack's version."""
    for line in (ROOT / 'keycloak/Containerfile').read_text().splitlines():
        if line.startswith('FROM '):
            return line.split()[1]
    raise RuntimeError('keycloak/Containerfile has no FROM line')


def make_pfx(work, password):
    """A demo CA, a signing key and certificate it issued, and a PFX holding both; return the files."""
    def openssl(*argv):
        run('openssl', *argv)
    openssl('req', '-x509', '-newkey', 'rsa:3072', '-noenc', '-keyout', str(work / 'ca.key'),
            '-out', str(work / 'ca.crt'), '-days', '30', '-subj', '/CN=Todo spike organisation CA',
            '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign')
    openssl('req', '-new', '-newkey', 'rsa:2048', '-noenc', '-keyout', str(work / 'signing.key'),
            '-out', str(work / 'signing.csr'), '-subj', '/CN=Todo spike token signing')
    (work / 'signing.ext').write_text('basicConstraints=critical,CA:FALSE\n'
                                      'keyUsage=critical,digitalSignature\n')
    openssl('x509', '-req', '-in', str(work / 'signing.csr'), '-CA', str(work / 'ca.crt'),
            '-CAkey', str(work / 'ca.key'), '-set_serial', '1', '-days', '30',
            '-extfile', str(work / 'signing.ext'), '-out', str(work / 'signing.crt'))
    (work / 'password').write_text(password)
    # OpenSSL 3's default PKCS#12: AES-256-CBC and PBKDF2, as a current tool would make it.
    openssl('pkcs12', '-export', '-name', ALIAS, '-inkey', str(work / 'signing.key'),
            '-in', str(work / 'signing.crt'), '-certfile', str(work / 'ca.crt'),
            '-passout', f'file:{work / "password"}', '-out', str(work / 'signing.pfx'))
    (work / 'password').unlink()
    return work / 'signing.pfx', work / 'signing.crt'


def kube_secret(name, files):
    """A raw Podman secret holding a Kube Secret whose data is files' bytes, base64-encoded."""
    payload = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name},
               'data': {key: base64.b64encode(value).decode() for key, value in files.items()}}
    run('podman', 'secret', 'create', name, '-', input=json.dumps(payload).encode())


POD_YAML = f"""\
apiVersion: v1
kind: Pod
metadata:
  name: {POD}
spec:
  restartPolicy: Never
  containers:
    - name: keycloak
      image: IMAGE
      args: [start-dev]
      ports:
        - containerPort: 8080
          hostPort: {PORT}
          hostIP: 127.0.0.1
      env:
        - name: KC_BOOTSTRAP_ADMIN_USERNAME
          value: admin
        - name: KC_BOOTSTRAP_ADMIN_PASSWORD
          valueFrom:
            secretKeyRef:
              name: {ADMIN_SECRET}
              key: password
        - name: KC_VAULT
          value: file
        - name: KC_VAULT_DIR
          value: {VAULT_DIR}
      securityContext:
        runAsUser: 1000
        runAsGroup: 0
        allowPrivilegeEscalation: false
        capabilities:
          drop: [ALL]
      volumeMounts:
        - name: signing
          mountPath: {SIGNING_DIR}
          readOnly: true
        - name: vault
          mountPath: {VAULT_DIR}
          readOnly: true
  volumes:
    - name: signing
      secret:
        secretName: {PFX_SECRET}
        defaultMode: 0440
        items:
          - key: signing.pfx
            path: signing.pfx
    - name: vault
      secret:
        secretName: {VAULT_SECRET}
        defaultMode: 0440
        items:
          - key: password
            path: {REALM}_{VAULT_KEY}
"""


def http(path, method='GET', data=None, token=None, form=False):
    """One request to Keycloak; return (status, headers, parsed JSON or None)."""
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    body = None
    if data is not None:
        body = (urlencode(data) if form else json.dumps(data)).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    try:
        with urlopen(Request(BASE + path, data=body, headers=headers, method=method), timeout=30) as response:
            text = response.read()
            return response.status, response.headers, json.loads(text) if text else None
    except HTTPError as error:
        text = error.read()
        try:
            return error.code, error.headers, json.loads(text) if text else None
        except ValueError:
            return error.code, error.headers, text.decode(errors='replace')


def wait_for_keycloak():
    """Wait until the master realm answers; Keycloak's first start-dev takes a while."""
    for _ in range(90):
        try:
            if http('/realms/master')[0] == 200:
                return
        except (URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(2)
    raise RuntimeError('Keycloak did not answer within three minutes')


def admin_token(password):
    status, _headers, reply = http('/realms/master/protocol/openid-connect/token', 'POST', {
        'grant_type': 'password', 'client_id': 'admin-cli', 'username': 'admin', 'password': password,
    }, form=True)
    if status != 200 or not isinstance(reply, dict):
        raise RuntimeError(f'admin login failed: {status} {reply}')
    return reply['access_token']


def expect(status, wanted, what, reply):
    if status != wanted:
        raise RuntimeError(f'{what}: HTTP {status}, expected {wanted}: {reply}')


def make_realm(token, user_password):
    """A realm with a public client for the password grant and one user; return the realm's id."""
    status, _h, reply = http('/admin/realms', 'POST', {'realm': REALM, 'enabled': True}, token)
    expect(status, 201, 'create realm', reply)
    status, _h, reply = http(f'/admin/realms/{REALM}/clients', 'POST', {
        'clientId': 'spike', 'publicClient': True, 'directAccessGrantsEnabled': True,
        'standardFlowEnabled': False}, token)
    expect(status, 201, 'create client', reply)
    status, _h, reply = http(f'/admin/realms/{REALM}/users', 'POST', {
        'username': 'spike', 'enabled': True, 'email': 'spike@example.test', 'emailVerified': True,
        'firstName': 'Spike', 'lastName': 'Test',
        'credentials': [{'type': 'password', 'value': user_password, 'temporary': False}]}, token)
    expect(status, 201, 'create user', reply)
    status, _h, reply = http(f'/admin/realms/{REALM}', token=token)
    expect(status, 200, 'read realm', reply)
    assert isinstance(reply, dict)
    return reply['id']


def add_key(token, realm_id, name, keystore, password):
    """Add a java-keystore key provider; return (HTTP status, component id or the error)."""
    status, headers, reply = http(f'/admin/realms/{REALM}/components', 'POST', {
        'name': name, 'providerId': 'java-keystore', 'providerType': 'org.keycloak.keys.KeyProvider',
        'parentId': realm_id,
        'config': {'priority': ['200'], 'enabled': ['true'], 'active': ['true'], 'algorithm': ['RS256'],
                   'keystore': [keystore], 'keystoreType': ['PKCS12'], 'keystorePassword': [password],
                   'keyAlias': [ALIAS], 'keyPassword': [password]}}, token)
    if status == 201:
        return status, headers['Location'].rsplit('/', 1)[1]
    return status, reply


def active_key(token, component):
    """The realm's active RS256 key from component, or None."""
    _status, _h, reply = http(f'/admin/realms/{REALM}/keys', token=token)
    assert isinstance(reply, dict)
    for key in reply.get('keys', []):
        if key.get('providerId') == component and key.get('status') == 'ACTIVE' and key.get('algorithm') == 'RS256':
            return key
    return None


def refused(token, realm_id, name, keystore, password):
    """True if Keycloak does not get an active key from this keystore and password."""
    status, result = add_key(token, realm_id, name, keystore, password)
    if status != 201:
        print(f'  {name}: refused with HTTP {status}: {result}')
        return True
    key = active_key(token, result)
    http(f'/admin/realms/{REALM}/components/{result}', 'DELETE', token=token)
    print(f'  {name}: accepted (HTTP 201), active key: {bool(key)}')
    return key is None


def b64url(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def main():
    work = Path(tempfile.mkdtemp(prefix='keycloak-pfx-spike-'))
    results = []

    def result(name, ok, detail='', required=True):
        results.append((name, ok, required))
        print(f'RESULT {name}: {"yes" if ok else "NO"}{" (" + detail + ")" if detail else ""}')

    pfx_password = secrets.token_urlsafe(24)
    admin_password = secrets.token_urlsafe(24)
    user_password = secrets.token_urlsafe(24)
    image = keycloak_image()
    pod_yaml = work / 'pod.yaml'
    try:
        pfx, certificate = make_pfx(work, pfx_password)
        der = run('openssl', 'x509', '-in', str(certificate), '-outform', 'DER')
        run('podman', 'pull', '--quiet', image)
        kube_secret(PFX_SECRET, {'signing.pfx': pfx.read_bytes()})
        kube_secret(VAULT_SECRET, {'password': pfx_password.encode()})
        kube_secret(ADMIN_SECRET, {'password': admin_password.encode()})
        pod_yaml.write_text(POD_YAML.replace('IMAGE', json.dumps(image)))
        run('podman', 'kube', 'play', str(pod_yaml))

        # 2. The file secret in the container.
        mounted = run('podman', 'exec', CONTAINER, 'cat', f'{SIGNING_DIR}/signing.pfx', check=False)
        result('the PFX arrives byte for byte and Keycloak\'s user can read it', mounted == pfx.read_bytes(),
               f'{len(mounted)} of {len(pfx.read_bytes())} bytes')
        listing = run('podman', 'exec', CONTAINER, 'ls', '-ln', SIGNING_DIR, VAULT_DIR, check=False)
        print('  mounts as the container sees them:\n    '
              + listing.decode(errors='replace').strip().replace('\n', '\n    '))
        print('  as uid:gid ' + run('podman', 'exec', CONTAINER, 'id', check=False).decode().strip())

        wait_for_keycloak()
        token = admin_token(admin_password)
        realm_id = make_realm(token, user_password)

        # 3. The key provider, first with the password from the vault.
        status, vault_result = add_key(token, realm_id, 'pfx-vault', f'{SIGNING_DIR}/signing.pfx',
                                       '${vault.' + VAULT_KEY + '}')
        component = vault_result if status == 201 and active_key(token, vault_result) else None
        result('the password can come from the file vault (never through the API)', component is not None,
               f'HTTP {status}' + ('' if component else f': {vault_result}'), required=False)
        if component is None:
            if status == 201:
                http(f'/admin/realms/{REALM}/components/{vault_result}', 'DELETE', token=token)
            status, plain_result = add_key(token, realm_id, 'pfx-plain', f'{SIGNING_DIR}/signing.pfx', pfx_password)
            component = plain_result if status == 201 else None
            result('the password works as a plain value through the admin API', component is not None,
                   f'HTTP {status}' + ('' if component else f': {plain_result}'))
        if component is None:
            raise RuntimeError('Keycloak has no key from the PFX')
        _status, _h, stored = http(f'/admin/realms/{REALM}/components/{component}', token=token)
        assert isinstance(stored, dict)
        result('the admin API never returns the password',
               pfx_password not in json.dumps(stored), f'keystorePassword reads {stored["config"]["keystorePassword"]}')

        key = active_key(token, component)
        expected = base64.b64encode(der).decode()
        result('the active key is the PFX key and certificate', bool(key) and key['certificate'] == expected,
               f'kid {key["kid"]}' if key else 'no active key')
        assert key

        # 4. A token signed with it.
        status, _h, reply = http(f'/realms/{REALM}/protocol/openid-connect/token', 'POST', {
            'grant_type': 'password', 'client_id': 'spike', 'username': 'spike', 'password': user_password,
            'scope': 'openid'}, form=True)
        expect(status, 200, 'user login', reply)
        assert isinstance(reply, dict)
        header, payload, signature = reply['access_token'].split('.')
        kid = json.loads(b64url(header))['kid']
        result('the token is signed with the PFX key (kid)', kid == key['kid'], f'kid {kid}')
        _status, _h, jwks = http(f'/realms/{REALM}/protocol/openid-connect/certs')
        assert isinstance(jwks, dict)
        published = next((entry for entry in jwks['keys'] if entry['kid'] == kid), {})
        result('the JWKS publishes the PFX certificate (x5c)', published.get('x5c', [None])[0] == expected)
        (work / 'public.pem').write_bytes(run('openssl', 'x509', '-in', str(certificate), '-noout', '-pubkey'))
        (work / 'signed').write_bytes(f'{header}.{payload}'.encode())
        (work / 'signature').write_bytes(b64url(signature))
        verified = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(work / 'public.pem'),
                                   '-signature', str(work / 'signature'), str(work / 'signed')],
                                  capture_output=True, check=False).returncode == 0
        result('openssl verifies the signature with the PFX certificate', verified)
        chain = subprocess.run(['openssl', 'verify', '-CAfile', str(work / 'ca.crt'), str(certificate)],
                               capture_output=True, check=False).returncode == 0
        result('the signing certificate chains to the organisation CA', chain)

        # 5. What must stop an install.
        result('a wrong password is refused',
               refused(token, realm_id, 'pfx-wrong-password', f'{SIGNING_DIR}/signing.pfx', 'wrong-password'))
        result('a missing file is refused',
               refused(token, realm_id, 'pfx-missing-file', f'{SIGNING_DIR}/missing.pfx', pfx_password))
    except Exception as error:  # the spike reports what it got to, then fails
        print(f'ERROR: {error}', file=sys.stderr)
        print(run('podman', 'logs', '--tail', '60', CONTAINER, check=False).decode(errors='replace'), file=sys.stderr)
        results.append(('completed', False, True))
    finally:
        if pod_yaml.exists():
            run('podman', 'kube', 'down', str(pod_yaml), check=False)
        for name in (PFX_SECRET, VAULT_SECRET, ADMIN_SECRET):
            run('podman', 'secret', 'rm', name, check=False)
        run('rm', '-rf', str(work))
    failed = [name for name, ok, required in results if required and not ok]
    print('Spike ' + ('FAILED: ' + '; '.join(failed) if failed else 'passed'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
