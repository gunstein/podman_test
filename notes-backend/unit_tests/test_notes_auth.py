import importlib
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
with patch.dict(os.environ, {'SERVE_FRONTEND': 'false'}):
    main = importlib.import_module('notes-backend.main')


class NotesAudienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def test_same_realm_tokens_require_the_notes_audience(self):
        issuer = 'https://todo.test:8443/auth/realms/todo'
        jwks = Mock()
        jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key=self.key.public_key())
        with patch.dict(os.environ, {'OIDC_ISSUER': issuer, 'OIDC_JWKS_URL': 'http://keycloak/certs',
                                     'OIDC_AUDIENCE': 'notes-frontend'}), \
                patch.object(main, 'get_jwks_client', return_value=jwks):
            for audience in ('notes-frontend', 'todo-frontend'):
                claims = {'iss': issuer, 'aud': audience, 'exp': int(time.time()) + 60, 'sub': 'same-user'}
                token = jwt.encode(claims, self.key, algorithm='RS256')
                if audience == 'notes-frontend':
                    self.assertEqual(main.validate_access_token(token)['sub'], 'same-user')
                else:
                    with self.assertRaises(jwt.InvalidAudienceError):
                        main.validate_access_token(token)
