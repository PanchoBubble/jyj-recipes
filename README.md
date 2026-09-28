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
| `make test` | pytest and vitest (DB tests skip if the test database is unreachable) |
| `make migrate` | `alembic upgrade head` against `DATABASE_URL` |
| `make migration name="..."` | autogenerate an Alembic revision from the models |
| `make ca-export` | copy Caddy's LAN root CA to `./caddy-root.crt` (stack must be up) |

Health check: `curl http://127.0.0.1:8000/healthz` returns `{"status":"ok"}`. `/readyz` also pings the database and returns 503 `{"status":"unavailable"}` with no details when it is down; it is for internal checks and is not proxied under `/api`. Through Caddy the same health check is `https://<JYJ_HOSTNAME>/api/healthz`.

## HTTPS on the LAN

Caddy serves the app on `https://$JYJ_HOSTNAME` (default `recipes.home.arpa`) with its own internal CA; HTTP redirects to HTTPS. Voice input needs this secure context. Device DNS and CA install steps, plus the DNS-01 and Tailscale alternatives, are in [docs/TLS.md](docs/TLS.md).

## Database

Settings load from env (and `.env`) via `jyj.config.Settings`. Backend tests use `TEST_DATABASE_URL` (default `postgresql+psycopg://jyj:jyj@127.0.0.1:55432/jyj_test`, the compose `db-test` service), run migrations once per session and roll back every test. Import new model modules in `backend/src/jyj/models/__init__.py` so autogenerate sees them.

## Accounts

There is no signup endpoint. Manage household accounts with the CLI (passwords are prompted, never passed as arguments; reset and disable revoke existing sessions):

```sh
cd backend && uv run jyj users create alice --display-name "Alice"
uv run jyj users reset-password alice
uv run jyj users disable alice
uv run jyj users list
```

The API uses a `jyj_session` HttpOnly cookie and requires `X-Requested-With: jyj` on every non-GET request under `/api/v1`.

## Config

Copy `.env.example` to `.env` and replace the placeholders. `.env` is git-ignored; never commit real secrets.
