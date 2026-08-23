from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import InvalidAlgorithmError, InvalidAudienceError
from jwt.exceptions import InvalidIssuerError, InvalidSignatureError
from jwt.exceptions import ExpiredSignatureError

from backend.main import get_jwks_client, validate_access_token


ISSUER = "https://issuer.example/realms/todo"
AUDIENCE = "todo-frontend"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class SigningKey:
    key = PRIVATE_KEY.public_key()


class JwksClient:
    def get_signing_key_from_jwt(self, _token):
        return SigningKey()


@pytest.fixture(autouse=True)
def oidc_configuration(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_JWKS_URL", "https://issuer.example/jwks")
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setattr("backend.main.get_jwks_client", lambda _url: JwksClient())


def token(**overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "test-user",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256")


def test_valid_access_token():
    assert validate_access_token(token())["sub"] == "test-user"


@pytest.mark.parametrize(
    ("changed_claims", "expected_error"),
    [
        ({"iss": "https://wrong.example"}, InvalidIssuerError),
        ({"aud": "wrong-audience"}, InvalidAudienceError),
        ({"exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, ExpiredSignatureError),
    ],
)
def test_invalid_claims_are_rejected(changed_claims, expected_error):
    with pytest.raises(expected_error):
        validate_access_token(token(**changed_claims))


def test_invalid_signature_is_rejected():
    forged = jwt.encode(jwt.decode(token(), options={"verify_signature": False}), OTHER_PRIVATE_KEY, algorithm="RS256")
    with pytest.raises(InvalidSignatureError):
        validate_access_token(forged)


def test_wrong_algorithm_is_rejected():
    forged = jwt.encode(
        {
            "sub": "test-user",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "not-the-rsa-key-but-at-least-32-bytes-long",
        algorithm="HS256",
    )
    with pytest.raises(InvalidAlgorithmError):
        validate_access_token(forged)


def test_jwks_client_is_cached():
    get_jwks_client.cache_clear()
    first = get_jwks_client("https://issuer.example/jwks")
    second = get_jwks_client("https://issuer.example/jwks")
    assert first is second
    get_jwks_client.cache_clear()
