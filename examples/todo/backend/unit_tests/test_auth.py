"""The access-token contract of the Todo API (docs/IDENTITY.md, section 2), without a database."""
import importlib
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
with mock.patch.dict(os.environ, {"SERVE_FRONTEND": "false"}):
    main = importlib.import_module(Path(__file__).resolve().parents[1].name + ".main")

ISSUER = "https://issuer.example/realms/todo"
AUDIENCE = "todo-api"
CLIENT = "todo-frontend"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class JwksClient:
    """The signing keys: only PRIVATE_KEY's public half."""

    def get_signing_key_from_jwt(self, _token):
        return mock.Mock(key=PRIVATE_KEY.public_key())


def token(key=PRIVATE_KEY, algorithm="RS256", drop=(), **overrides):
    now = datetime.now(timezone.utc)
    claims = {"sub": "test-user", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + timedelta(minutes=5)}
    claims.update(overrides)
    for name in drop:
        del claims[name]
    return jwt.encode(claims, key, algorithm=algorithm)


class AccessTokenTests(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {"OIDC_ISSUER": ISSUER, "OIDC_JWKS_URL": "https://issuer.example/jwks",
                                                   "OIDC_AUDIENCE": AUDIENCE})
        environment.start()
        self.addCleanup(environment.stop)
        jwks = mock.patch.object(main, "get_jwks_client", lambda _url: JwksClient())
        jwks.start()
        self.addCleanup(jwks.stop)

    def test_an_access_token_for_this_api_is_accepted(self):
        self.assertEqual(main.validate_access_token(token())["sub"], "test-user")
        # Keycloak may add more audiences; this API's must be one of them.
        self.assertEqual(main.validate_access_token(token(aud=["account", AUDIENCE]))["sub"], "test-user")

    def test_wrong_claims_are_refused(self):
        for overrides, error in (({"iss": "https://wrong.example"}, jwt.InvalidIssuerError),
                                 ({"aud": "wrong-audience"}, jwt.InvalidAudienceError),
                                 # Another app's access token, from the same realm and login.
                                 ({"aud": "notes-api"}, jwt.InvalidAudienceError),
                                 ({"exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
                                  jwt.ExpiredSignatureError)):
            with self.subTest(overrides=overrides), self.assertRaises(error):
                main.validate_access_token(token(**overrides))

    def test_an_id_token_is_refused(self):
        # Keycloak's ID token for the frontend: aud is the client, typ ID, azp the client.
        with self.assertRaises(jwt.InvalidAudienceError):
            main.validate_access_token(token(aud=CLIENT, typ="ID", azp=CLIENT))

    def test_a_missing_claim_a_foreign_key_or_another_algorithm_is_refused(self):
        for name in ("exp", "sub", "aud", "iss"):
            with self.subTest(missing=name), self.assertRaises(jwt.PyJWTError):
                main.validate_access_token(token(drop=(name,)))
        with self.assertRaises(jwt.InvalidSignatureError):
            main.validate_access_token(token(key=OTHER_PRIVATE_KEY))
        with self.assertRaises(jwt.InvalidAlgorithmError):
            main.validate_access_token(token(key="not-the-rsa-key-but-at-least-32-bytes-long", algorithm="HS256"))

    def test_without_an_audience_nothing_is_accepted(self):
        with mock.patch.dict(os.environ, {"OIDC_AUDIENCE": ""}), \
                self.assertRaisesRegex(RuntimeError, "OIDC configuration is incomplete"):
            main.validate_access_token(token())


class JwksClientTests(unittest.TestCase):
    def test_the_jwks_client_is_cached(self):
        main.get_jwks_client.cache_clear()
        self.addCleanup(main.get_jwks_client.cache_clear)
        self.assertIs(main.get_jwks_client("https://issuer.example/jwks"),
                      main.get_jwks_client("https://issuer.example/jwks"))


if __name__ == "__main__":
    unittest.main()
