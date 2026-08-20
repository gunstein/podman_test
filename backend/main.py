from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


app = FastAPI(title="Todo Demo")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/todos")
def get_todos() -> list[dict[str, object]]:
    return [
        {
            "id": 1,
            "title": "Learn Podman",
            "completed": False,
        }
    ]


frontend_directory = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=frontend_directory, html=True), name="frontend")
