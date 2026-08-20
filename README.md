# Todo demo

A minimal Todo application with a FastAPI backend and a plain HTML, CSS and JavaScript frontend.

## Run locally

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the backend dependencies:

```bash
python -m pip install -r backend/requirements.txt
```

Start the application from the project root:

```bash
uvicorn backend.main:app --reload
```

Open <http://127.0.0.1:8000> in a browser. The health endpoint is available at <http://127.0.0.1:8000/health>.
