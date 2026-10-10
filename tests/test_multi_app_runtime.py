"""Render app manifests and enforce independent credentials and persistent storage."""
import unittest

import yaml

from tests.runtime_fixture import RUNTIME


class IndependentAppChartsTests(unittest.TestCase):
    def test_each_app_owns_its_database_pvcs_secrets_and_roles(self):
        all_claims = []
        for name, postgres_file, app_file, config_file in (
            ('todo', 'todo-postgres.yaml', 'todo-app.yaml', 'todo-config.yaml'),
            ('notes', 'notes-postgres.yaml', 'notes-app.yaml', 'notes-config.yaml'),
        ):
            documents = (list(yaml.safe_load_all((RUNTIME / postgres_file).read_text()))
                         + list(yaml.safe_load_all((RUNTIME / app_file).read_text()))
                         + list(yaml.safe_load_all((RUNTIME / config_file).read_text())))
            pods = {d['metadata']['name']: d for d in documents if d['kind'] == 'Pod'}
            self.assertEqual(set(pods), {name + '-app', name + '-postgres'})
            claims = {d['metadata']['name']: d for d in documents
                      if d['kind'] == 'PersistentVolumeClaim'}
            self.assertEqual(set(claims), {name + '-postgres-data', name + '-postgres-backup'})
            all_claims.append(set(claims))
            for claim in claims.values():
                annotations = claim['metadata']['annotations']
                self.assertEqual(annotations['volume.podman.io/uid'], '999')
                self.assertEqual(annotations['volume.podman.io/gid'], '999')
            postgres = pods[name + '-postgres']['spec']
            container = postgres['containers'][0]
            self.assertEqual(container['securityContext']['runAsUser'], 999)
            self.assertEqual({v['mountPath'] for v in container['volumeMounts']}, {
                '/var/lib/postgresql/data', '/var/lib/postgresql/backup',
                '/run/secrets/' + name + '-postgres'})
            secret = next(v['secret']['secretName'] for v in postgres['volumes'] if 'secret' in v)
            self.assertEqual(secret, name + '-kube-postgres-secret')
            application = pods[name + '-app']['spec']
            self.assertEqual({v['secret']['secretName'] for v in application['volumes']}, {
                name + '-kube-migrator-secret', name + '-kube-backend-secret'})
            migration = application['initContainers'][0]
            user = next(e['value'] for e in migration['env'] if e['name'] == 'DATABASE_USER')
            self.assertEqual(user, name + '_migrator')
            backend = next(d for d in documents if d['metadata']['name'] == name + '-backend-config')
            self.assertEqual(backend['data']['DATABASE_HOST'], name + '-postgres')
            runtime = next(e['value'] for e in application['containers'][0]['env']
                           if e['name'] == 'DATABASE_USER')
            self.assertEqual(runtime, name + '_app')
            self.assertEqual(backend['data']['OIDC_AUDIENCE'], name + '-frontend')
            self.assertEqual(backend['data']['OIDC_ISSUER'], 'https://auth.test:8443/auth/realms/todo')
        self.assertFalse(all_claims[0] & all_claims[1])

    def test_no_container_sets_a_variable_both_in_env_and_from_a_config_map(self):
        # Podman 5.7 lets envFrom win over env for the same name (Kubernetes lets env
        # win), so a variable set in both places depends on the Podman version.
        documents = [d for path in sorted(RUNTIME.glob('*.yaml'))
                     for d in yaml.safe_load_all(path.read_text()) if d]
        config_maps = {d['metadata']['name']: d.get('data', {}) for d in documents
                       if d['kind'] == 'ConfigMap'}
        checked = 0
        for pod in (d for d in documents if d['kind'] == 'Pod'):
            for container in pod['spec'].get('initContainers', []) + pod['spec']['containers']:
                from_maps = {key for source in container.get('envFrom', [])
                             for key in config_maps[source['configMapRef']['name']]}
                explicit = {variable['name'] for variable in container.get('env', [])}
                self.assertEqual(from_maps & explicit, set(), container['name'])
                checked += bool(from_maps and explicit)
        self.assertGreaterEqual(checked, 4)

    def test_shared_proxy_routes_both_hostnames_and_uses_one_san_certificate(self):
        documents = list(yaml.safe_load_all((RUNTIME / 'shared-proxy.yaml').read_text()))
        config = next(d['data']['nginx.conf'] for d in documents
                      if d['metadata']['name'] == 'shared-nginx-config')
        environment = next(d['data'] for d in documents if d['metadata']['name'] == 'shared-nginx-env')
        self.assertEqual(set(environment['APP_TLS_HOSTNAMES'].split()), {'todo.test', 'notes.test', 'help.test'})
        for name in ('todo', 'notes'):
            self.assertIn('server_name ' + name + '.test;', config)
            self.assertIn('server ' + name + '-app:8000 resolve;', config)
            self.assertIn('server ' + name + '-app:8080 resolve;', config)
        # Help is static: one site container, no login.
        self.assertIn('server_name help.test;', config)
        self.assertIn('server help-app:8080 resolve;', config)
        # Keycloak's own server and each login app's send /auth/ to Keycloak; every server has the one certificate.
        self.assertIn('server_name auth.test;', config)
        self.assertEqual(config.count('proxy_pass http://shared_keycloak;'), 3)
        self.assertEqual(config.count('ssl_certificate /var/lib/platform-tls/server.crt;'), 4)
        self.assertEqual(config.count('ssl_certificate_key /var/lib/platform-tls/server.key;'), 4)
