# App

A web app (`frontend/`, React) with its own server (`backend/`, FastAPI + SQLite). Needs Node.js (npm) and [uv](https://docs.astral.sh/uv/).

## Run it
```
python3 dev.py install   # once: installs the libraries for both parts
python3 dev.py           # starts both; Ctrl-C stops both
```
Then open the address the frontend prints (usually http://localhost:5173). The server's own page with every endpoint is http://127.0.0.1:8000/docs.

Or run each part in its own terminal:
```
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```

## Tests
```
cd frontend && npm test
cd frontend && npm run build    # also checks the TypeScript types
cd backend && uv run pytest -q
```

## What is where
- `frontend/` - what people see in the browser. Details in `frontend/README.md`.
- `backend/` - the server and its database. Details in `backend/README.md`.
- In the frontend, call the backend with paths that start with `/api`: `fetch("/api/items")` reaches `GET /items` on the backend (set up in `frontend/vite.config.ts`).
- `dev.py` - starts both parts together.
