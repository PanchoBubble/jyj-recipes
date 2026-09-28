# jyj recipes

Self-hosted household app for recipes, stock, a meal calendar and shopping lists, with an optional chat and voice assistant. Runs on a Raspberry Pi on the LAN.

Design, stack and milestones: [docs/PLAN.md](docs/PLAN.md). Issues are tracked with `bd` (see [AGENTS.md](AGENTS.md)).

## Layout

- `backend/`: Python 3.12, FastAPI, managed with [uv](https://docs.astral.sh/uv/) (`uv.lock`)
- `frontend/`: React + TypeScript + Vite, Tailwind v4, shadcn/ui, managed with **npm** (`package-lock.json`)

## Requirements

- uv (installs Python 3.12 itself if missing)
- Node.js 24 LTS + npm

## Make targets

| Target | What it does |
|--------|--------------|
| `make install` | `uv sync --locked` and `npm ci` |
| `make dev` | backend on http://127.0.0.1:8000 (reload), frontend on http://localhost:5173 |
| `make lint` | ruff check + format check, eslint, tsc |
| `make format` | ruff autofix + format |
| `make test` | pytest and vitest |

Health check: `curl http://127.0.0.1:8000/healthz` returns `{"status":"ok"}`.

## Config

Copy `.env.example` to `.env` and replace the placeholders. `.env` is git-ignored; never commit real secrets.
