from fastapi.testclient import TestClient

from backend.main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness():
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_frontend_is_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "Todo Demo" in response.text


def test_todo_crud():
    assert client.get("/api/todos").json() == []

    created = client.post("/api/todos", json={"title": "Test Todo"})
    assert created.status_code == 201
    todo = created.json()
    assert todo == {"id": 1, "title": "Test Todo", "completed": False}

    updated = client.put(
        f"/api/todos/{todo['id']}",
        json={"title": "Updated Todo", "completed": True},
    )
    assert updated.status_code == 200
    assert updated.json()["completed"] is True

    deleted = client.delete(f"/api/todos/{todo['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/todos").json() == []


def test_missing_todo_returns_404():
    update = client.put(
        "/api/todos/999",
        json={"title": "Missing", "completed": False},
    )
    assert update.status_code == 404
    assert client.delete("/api/todos/999").status_code == 404


def test_empty_title_is_rejected():
    response = client.post("/api/todos", json={"title": ""})
    assert response.status_code == 422
