"""Render app charts and enforce independent credentials and persistent storage."""
import os
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class IndependentAppChartsTests(unittest.TestCase):
    def test_each_app_owns_its_database_pvcs_secrets_and_roles(self):
        all_claims = []
        for name in ('todo', 'notes'):
            result = subprocess.run([
                os.environ.get('HELM', 'helm'), 'template', name,
                str(ROOT / 'deploy/charts' / name), '--values',
                str(ROOT / 'deploy/environments/prod/values.yaml'),
            ], check=True, capture_output=True, text=True)
            documents = list(yaml.safe_load_all(result.stdout))
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
            self.assertEqual(backend['data']['DATABASE_USER'], name + '_app')
            self.assertEqual(backend['data']['OIDC_AUDIENCE'], name + '-frontend')
            self.assertEqual(backend['data']['OIDC_ISSUER'], 'https://todo.test:8443/auth/realms/todo')
        self.assertFalse(all_claims[0] & all_claims[1])

    def test_shared_proxy_routes_both_hostnames_and_uses_one_san_certificate(self):
        from tests.runtime_fixture import RUNTIME
        documents = list(yaml.safe_load_all((RUNTIME / 'shared-proxy.yaml').read_text()))
        config = next(d['data']['nginx.conf'] for d in documents
                      if d['metadata']['name'] == 'shared-nginx-config')
        environment = next(d['data'] for d in documents if d['metadata']['name'] == 'shared-nginx-env')
        self.assertEqual(set(environment['APP_TLS_HOSTNAMES'].split()), {'todo.test', 'notes.test'})
        for name in ('todo', 'notes'):
            self.assertIn('server_name ' + name + '.test;', config)
            self.assertIn('server ' + name + '-app:8000 resolve;', config)
            self.assertIn('server ' + name + '-app:8080 resolve;', config)
        self.assertEqual(config.count('proxy_pass http://shared_keycloak;'), 2)
        self.assertEqual(config.count('ssl_certificate /var/lib/todo-tls/server.crt;'), 2)
        self.assertEqual(config.count('ssl_certificate_key /var/lib/todo-tls/server.key;'), 2)
