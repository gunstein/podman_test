import pytest
from fastapi.testclient import TestClient

from backend.main import app, connect


client = TestClient(app)
AUTHORIZATION = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def valid_access_token(monkeypatch):
    monkeypatch.setattr(
        "backend.main.validate_access_token",
        lambda token: {"sub": "test-user"},
    )


def test_connect_uses_password_file(monkeypatch, tmp_path):
    password_file = tmp_path / "database-password"
    password_file.write_text("secret-from-file\n")
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(password_file))
    monkeypatch.setenv("DATABASE_HOST", "database.example")
    monkeypatch.setattr("backend.main.psycopg.connect", fake_connect)

    connect()

    assert captured["host"] == "database.example"
    assert captured["password"] == "secret-from-file"


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness():
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_when_database_is_unavailable(monkeypatch):
    def unavailable():
        raise RuntimeError("Database unavailable")

    monkeypatch.setattr("backend.main.connect", unavailable)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_frontend_is_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "Todo Demo" in response.text


def test_todo_crud():
    assert client.get("/api/todos").json() == []

    created = client.post(
        "/api/todos", json={"title": "Test Todo"}, headers=AUTHORIZATION
    )
    assert created.status_code == 201
    todo = created.json()
    assert todo == {"id": 1, "title": "Test Todo", "completed": False}

    updated = client.put(
        f"/api/todos/{todo['id']}",
        json={"title": "Updated Todo", "completed": True},
        headers=AUTHORIZATION,
    )
    assert updated.status_code == 200
    assert updated.json()["completed"] is True

    deleted = client.delete(f"/api/todos/{todo['id']}", headers=AUTHORIZATION)
    assert deleted.status_code == 204
    assert client.get("/api/todos").json() == []


def test_missing_todo_returns_404():
    update = client.put(
        "/api/todos/999",
        json={"title": "Missing", "completed": False},
        headers=AUTHORIZATION,
    )
    assert update.status_code == 404
    assert client.delete("/api/todos/999", headers=AUTHORIZATION).status_code == 404


def test_invalid_titles_are_rejected():
    for title in ("", "   ", "x" * 201):
        response = client.post(
            "/api/todos", json={"title": title}, headers=AUTHORIZATION
        )
        assert response.status_code == 422


def test_writes_require_authentication():
    assert client.get("/api/todos").status_code == 200
    response = client.post("/api/todos", json={"title": "Protected"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
