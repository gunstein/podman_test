import os
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator


def connect():
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
        host=os.getenv("DATABASE_HOST", "todo-postgres"),
        port=os.getenv("DATABASE_PORT", "5432"),
        dbname=os.getenv("DATABASE_NAME", "todo"),
        user=os.getenv("DATABASE_USER", "todo"),
        password=password,
        row_factory=dict_row,
    )


app = FastAPI(title="Todo Demo")


class TodoCreate(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value


class TodoUpdate(TodoCreate):
    completed: bool


class Todo(TodoUpdate):
    id: int


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    try:
        with connect() as connection:
            connection.execute("SELECT 1")
    except (psycopg.Error, RuntimeError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from None
    return {"status": "ready"}


@app.get("/api/todos", response_model=list[Todo])
def get_todos():
    with connect() as connection:
        return connection.execute(
            "SELECT id, title, completed FROM todos ORDER BY id"
        ).fetchall()


@app.post("/api/todos", response_model=Todo, status_code=201)
def create_todo(todo: TodoCreate):
    with connect() as connection:
        return connection.execute(
            "INSERT INTO todos (title) VALUES (%s) RETURNING id, title, completed",
            (todo.title,),
        ).fetchone()


@app.put("/api/todos/{todo_id}", response_model=Todo)
def update_todo(todo_id: int, todo: TodoUpdate):
    with connect() as connection:
        result = connection.execute(
            "UPDATE todos SET title=%s, completed=%s WHERE id=%s RETURNING id,title,completed",
            (todo.title, todo.completed, todo_id),
        ).fetchone()
    if result is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return result


@app.delete("/api/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo(todo_id: int):
    with connect() as connection:
        result = connection.execute(
            "DELETE FROM todos WHERE id=%s RETURNING id", (todo_id,)
        ).fetchone()
    if result is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return Response(status_code=204)


if os.getenv("SERVE_FRONTEND", "true").lower() == "true":
    frontend = Path(__file__).resolve().parent.parent / "frontend"
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
