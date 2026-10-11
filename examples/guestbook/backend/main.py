"""Guestbook API. Reading, writing and deleting all require an access token."""
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import jwt
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator


def connect(connect_timeout=None):
    """Open a database connection that returns rows as dicts.

    DATABASE_URL wins if set (local development). Otherwise the password is
    read from DATABASE_PASSWORD_FILE, a mounted Podman secret, so it never
    appears in the environment or the Kube YAML. connect_timeout, in whole
    seconds, stops an attempt that hangs; without it libpq waits as long as
    the network lets it.
    """
    limit = {} if connect_timeout is None else {"connect_timeout": connect_timeout}
    url = os.getenv("DATABASE_URL")
    if url:
        return psycopg.connect(url, row_factory=dict_row, **limit)

    password_file = os.getenv("DATABASE_PASSWORD_FILE")
    if not password_file:
        raise RuntimeError("DATABASE_URL or DATABASE_PASSWORD_FILE must be set")
    try:
        password = Path(password_file).read_text().strip()
    except OSError as error:
        raise RuntimeError("Could not read database password file") from error
    if not password:
        raise RuntimeError("Database password file is empty")

    return psycopg.connect(
        host=os.getenv("DATABASE_HOST", "guestbook-postgres"),
        port=os.getenv("DATABASE_PORT", "5432"),
        dbname=os.getenv("DATABASE_NAME", "guestbook"),
        user=os.getenv("DATABASE_USER", "guestbook"),
        password=password,
        row_factory=dict_row,
        **limit,
    )


app = FastAPI(title="Guestbook")
bearer = HTTPBearer(auto_error=False)


@lru_cache
def get_jwks_client(jwks_url: str) -> PyJWKClient:
    """One cached client per JWKS URL, so Keycloak's signing keys are not fetched on every request."""
    return PyJWKClient(jwks_url)


def validate_access_token(token: str) -> dict:
    """Check the token's signature, issuer, audience and expiry; return its claims.

    The audience check means a token issued for another app is refused here,
    even though every app shares the same Keycloak realm and login, and so is
    an ID token: it names the frontend's client, never the API audience.
    """
    issuer = os.getenv("OIDC_ISSUER")
    jwks_url = os.getenv("OIDC_JWKS_URL")
    audience = os.getenv("OIDC_AUDIENCE")
    if not issuer or not jwks_url or not audience:
        raise RuntimeError("OIDC configuration is incomplete")
    signing_key = get_jwks_client(jwks_url).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=issuer,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )


def require_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict:
    """FastAPI dependency: the caller's token claims, or 401 if the token is missing or invalid."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return validate_access_token(credentials.credentials)
    except (PyJWTError, PyJWKClientError, RuntimeError):
        raise HTTPException(status_code=401, detail="Invalid access token") from None


class EntryCreate(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=4000)]

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message must not be blank")
        return value


class Entry(EntryCreate):
    id: int
    created_at: datetime


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    """Check the migrated table with the runtime role, without exposing entries."""
    try:
        with connect(connect_timeout=2) as connection:
            connection.execute("SELECT id, message, created_at FROM entries LIMIT 0")
    except (psycopg.Error, RuntimeError):
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ready"}


@app.get("/api/entries", response_model=list[Entry])
def get_entries(_user: Annotated[dict, Depends(require_user)]):
    with connect() as connection:
        return connection.execute(
            "SELECT id, message, created_at FROM entries ORDER BY id DESC"
        ).fetchall()


@app.post("/api/entries", response_model=Entry, status_code=201)
def create_entry(entry: EntryCreate, _user: Annotated[dict, Depends(require_user)]):
    with connect() as connection:
        return connection.execute(
            "INSERT INTO entries (message) VALUES (%s) RETURNING id, message, created_at",
            (entry.message,),
        ).fetchone()


@app.delete("/api/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(entry_id: int, _user: Annotated[dict, Depends(require_user)]):
    with connect() as connection:
        result = connection.execute(
            "DELETE FROM entries WHERE id=%s RETURNING id", (entry_id,)
        ).fetchone()
    if result is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return Response(status_code=204)
