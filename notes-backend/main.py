"""The Notes REST API: public reading, and writing for logged-in users.

GET /api/notes is public. Creating, changing and deleting need a Keycloak
access token issued for this app (notes-frontend). The backend checks the
token itself on every request; nginx only routes. It connects to PostgreSQL
as the notes_app role, which may only read and write rows. The schema belongs
to notes_migrator, and migrations run separately (migrate.py).
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import jwt
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator


def connect():
    """Open a database connection that returns rows as dicts.

    DATABASE_URL wins if set (local development). Otherwise the password is
    read from DATABASE_PASSWORD_FILE, a mounted Podman secret, so it never
    appears in the environment or the Kube YAML.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return psycopg.connect(url, row_factory=dict_row)

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
        host=os.getenv("DATABASE_HOST", "notes-postgres"),
        port=os.getenv("DATABASE_PORT", "5432"),
        dbname=os.getenv("DATABASE_NAME", "notes"),
        user=os.getenv("DATABASE_USER", "notes"),
        password=password,
        row_factory=dict_row,
    )


app = FastAPI(title="Notes Demo")
bearer = HTTPBearer(auto_error=False)


@lru_cache
def get_jwks_client(jwks_url: str) -> PyJWKClient:
    """One cached client per JWKS URL, so Keycloak's signing keys are not fetched on every request."""
    return PyJWKClient(jwks_url)


def validate_access_token(token: str) -> dict:
    """Check the token's signature, issuer, audience and expiry; return its claims.

    The audience check means a token issued for another app is refused here,
    even though every app shares the same Keycloak realm and login.
    """
    issuer = os.getenv("OIDC_ISSUER")
    jwks_url = os.getenv("OIDC_JWKS_URL")
    audience = os.getenv("OIDC_AUDIENCE", "notes-frontend")
    if not issuer or not jwks_url:
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


class NoteCreate(BaseModel):
    body: Annotated[str, Field(max_length=10000)] = ""
    title: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value


class NoteUpdate(NoteCreate):
    pass


class Note(NoteUpdate):
    id: int


@app.get("/health")
def health():
    """Liveness: the process answers. Does not touch the database."""
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    """Readiness: 503 until the database accepts a query."""
    try:
        with connect() as connection:
            connection.execute("SELECT 1")
    except (psycopg.Error, RuntimeError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from None
    return {"status": "ready"}


@app.get("/api/notes", response_model=list[Note])
def get_notes():
    with connect() as connection:
        return connection.execute(
            "SELECT id, title, body FROM notes ORDER BY id"
        ).fetchall()


@app.post("/api/notes", response_model=Note, status_code=201)
def create_note(note: NoteCreate, _user: Annotated[dict, Depends(require_user)]):
    with connect() as connection:
        return connection.execute(
            "INSERT INTO notes (title, body) VALUES (%s, %s) RETURNING id, title, body",
            (note.title, note.body),
        ).fetchone()


@app.put("/api/notes/{note_id}", response_model=Note)
def update_note(
    note_id: int,
    note: NoteUpdate,
    _user: Annotated[dict, Depends(require_user)],
):
    with connect() as connection:
        result = connection.execute(
            "UPDATE notes SET title=%s, body=%s WHERE id=%s RETURNING id,title,body",
            (note.title, note.body, note_id),
        ).fetchone()
    if result is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return result


@app.delete("/api/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: int, _user: Annotated[dict, Depends(require_user)]):
    with connect() as connection:
        result = connection.execute(
            "DELETE FROM notes WHERE id=%s RETURNING id", (note_id,)
        ).fetchone()
    if result is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return Response(status_code=204)


if os.getenv("SERVE_FRONTEND", "true").lower() == "true":
    frontend = Path(__file__).resolve().parent.parent / "notes-frontend"
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
