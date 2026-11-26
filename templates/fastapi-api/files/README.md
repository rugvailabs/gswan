# API

A backend server (Python + FastAPI) that keeps its data in a SQLite file. Needs [uv](https://docs.astral.sh/uv/).

## Run it
```
uv sync                                         # once: installs Python and the libraries into .venv/
uv run uvicorn app.main:app --reload --port 8000  # starts the server
uv run pytest -q                                # runs the tests
```
While it runs, open http://127.0.0.1:8000/docs to see and try every endpoint.

## What is where
- `app/main.py` - the endpoints: `GET /health`, and an example resource `items` (`POST /items`, `GET /items?limit=&offset=`, `GET /items/{id}`). Copy it for your own resources.
- `app/models.py` - the shapes of requests and responses (Pydantic). JSON keys are camelCase (`createdAt`).
- `app/db.py` - the database table and connection (SQLite, stored in `app.db`; set `APP_DB` to use another file).
- `tests/` - tests (pytest + FastAPI's TestClient); each test uses its own temporary database.
- `pyproject.toml` - dependencies and settings.

Conventions: ids have a prefix (`item_...`), list endpoints take `limit` and `offset`.
