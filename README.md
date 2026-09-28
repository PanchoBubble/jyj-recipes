# jyj recipes

Self-hosted household app for recipes, a pantry (what's at home), a meal calendar and shopping lists, with an optional chat and voice assistant. Runs on a Raspberry Pi on the LAN.

The pantry lives at `/pantry` in the app (`/stock` redirects there). Its API, tables and chat tools keep the internal name `stock` (`/api/v1/stock`, `get_stock`, `adjust_stock`, `set_stock`).

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

Health check: `curl http://127.0.0.1:8000/healthz` returns `{"status":"ok"}`. `/readyz` also pings the database and returns 503 `{"status":"unavailable"}` with no details when it is down; it is for internal checks and is not proxied under `/api`. The backend container healthcheck uses `/readyz`, and `web` waits for it. Through Caddy the same health check is `https://<JYJ_HOSTNAME>/api/healthz`.

## HTTPS on the LAN

Caddy serves the app on `https://$JYJ_HOSTNAME` (default `recipes.local`) with its own internal CA; HTTP redirects to HTTPS. Voice input needs this secure context. Device DNS and CA install steps, plus the DNS-01 and Tailscale alternatives, are in [docs/TLS.md](docs/TLS.md).

## Database

Settings load from env (and `.env`) via `jyj.config.Settings`. Backend tests use `TEST_DATABASE_URL` (default `postgresql+psycopg://jyj:jyj@127.0.0.1:55432/jyj_test`, the compose `db-test` service), create a fresh `jyj_test_<ts>_<pid>_<hex>` database on that server per pytest run (so parallel runs and worktrees never collide), run migrations into it once, roll back every test and drop it at the end. `TEST_DATABASE_REUSE=1` uses the URL's database as-is for debugging. `make test-db-prune` drops per-run databases older than 3 hours left by killed runs. Import new model modules in `backend/src/jyj/models/__init__.py` so autogenerate sees them.

## Accounts

There is no signup endpoint. Manage household accounts with the CLI (passwords are prompted, never passed as arguments; reset and disable revoke existing sessions):

```sh
cd backend && uv run jyj users create alice --display-name "Alice"
uv run jyj users reset-password alice
uv run jyj users disable alice
uv run jyj users list
```

The API uses a `jyj_session` HttpOnly cookie and requires `X-Requested-With: jyj` on every non-GET request under `/api/v1`.

## Chat

`POST /api/v1/chat/conversations/{id}/messages` runs one assistant turn and streams Server-Sent Events: `status`, `action` (executed or proposed, with its action id), `assistant`, `error` (sanitized) and a final `done`. Proposed actions (deletes, pantry overwrites) wait for `POST /api/v1/chat/actions/{id}/confirm` or `/reject`. A turn with more than 5 writes runs none of them: all of them (up to 25) are stored as proposals under one batch id and the stream sends a single `action` card with `tool: "batch"`, its `batch_id` and the member cards in `actions`. `POST /api/v1/chat/batches/{id}/confirm` runs the pending members in order and stops at the first one that does not succeed (the rest come back as `skipped` and stay proposed, so the batch can be confirmed again); `/reject` declines them all. Both return each member with its `outcome`, and every member can still be decided on its own through its action id. Each tool call runs under a Postgres `statement_timeout` (`CHAT_TOOL_TIMEOUT_SECONDS`, default 3) and ends as status `timeout` when it runs over. Chat text is kept 90 days: the `chat-purge` service runs `jyj chat purge` at start and every 24 h (the `chat_actions` audit rows are kept; see docs/BACKUP.md). To run it by hand:

```sh
docker compose exec backend jyj chat purge   # --days N to override
```

## Photo search (Openverse, optionally Pexels)

The recipe page and editor have a **Find a photo** button, and the assistant can do the same (`find_recipe_photos`, `set_recipe_photo`). It works out of the box with no signup: searches go to [Openverse](https://openverse.org), which indexes openly licensed photos (Flickr, Wikimedia and others). For more consistent food photos, add a free Pexels key and Pexels is used instead:

1. Get a free API key at https://www.pexels.com/api/.
2. Set `PEXELS_API_KEY` in `.env` and restart the backend.

`PHOTO_SEARCH_PROVIDER` picks the provider: `auto` (default: Pexels when a key is set, else Openverse), `openverse` or `pexels`. Forcing `pexels` without a key makes search answer 503 "photo search isn't configured".

Searches go through the backend (30 per user per minute by default, `PHOTO_SEARCH_RATE_LIMIT_PER_MINUTE`; results cached for 10 minutes; `PHOTO_SEARCH_TIMEOUT_SECONDS`, default 10, `PEXELS_TIMEOUT_SECONDS` still works). Openverse's anonymous API allows about 20 calls a minute and 200 a day per IP, so the household shares a budget of 20 calls a minute and gets a 429 "photo search is busy" past it. Openverse searches only licenses that allow adapting the photo (CC BY, BY-SA, BY-NC, BY-NC-SA, CC0 and public domain; no "no derivatives") and skips mature results.

- **Thumbnails.** Openverse thumbnails are proxied through `GET /api/v1/images/thumb?provider=openverse&id=` (logged in, size-capped, image type checked, cached in memory), so the browser only talks to this app. Pexels thumbnails load straight from `images.pexels.com`, which the Caddy CSP allows in `img-src`.
- **Imports.** Using a photo sends only its provider and id. The backend looks it up again at the provider and downloads only what that answer names: for Pexels only `https://images.pexels.com`; for Openverse the source image (for example on `live.staticflickr.com`) over https with an outbound guard (the host must resolve to public addresses only, re-checked on each of at most 3 redirects and again at connect time with the connection pinned to the checked address), falling back to Openverse's own full-size rendition. Downloads are capped at `PHOTO_MAX_BYTES` and go through the normal photo pipeline.
- **Credit.** An imported photo keeps its attribution (`photo_credit` on recipe responses). Pexels photos show "Photo by <photographer> on Pexels"; Openverse photos show "Photo: <title> by <creator>, <license>" with links to the source page, the creator and the license, as Creative Commons attribution asks. Uploading your own photo or removing it clears the credit.

## Speech to text

The backend image ships `whisper-cli` (whisper.cpp v1.9.1, built for armv8.2-a+dotprod on arm64) and a minimal static `ffmpeg`. Models are not baked in: `make whisper-download` (or `model=base`) fetches the multilingual ggml model from the whisper.cpp Hugging Face repo into the `whisper-models` volume and verifies its pinned SHA-256. `jyj.stt.get_transcriber()` converts uploads (webm/opus, mp4/aac, ogg, wav, mp3; max 5 MiB, 60 s) to 16 kHz mono WAV, runs one job at a time and deletes temp files afterwards. Audio is never persisted and transcripts are only logged at DEBUG. `tests/test_stt_integration.py` runs against a real install when `WHISPER_MODEL_PATH` points at a model. `POST /api/v1/chat/transcribe` (multipart field `audio`) returns `{text, language, confidence, low_confidence, duration_seconds}` for the user to review; the edited text is then sent as a normal chat message with `input: "voice"`. `STT_LOW_CONFIDENCE` (default 0.5) sets the flag and `STT_RATE_LIMIT_PER_MINUTE` (default 20) caps requests per user. `GET /api/v1/chat/health` reports `stt.available/detail/model`.

## Config

Copy `.env.example` to `.env` and replace the placeholders. `.env` is git-ignored; never commit real secrets.

## Deploy (Pi)

```sh
cp .env.example .env
openssl rand -hex 24      # paste as POSTGRES_PASSWORD
openssl rand -base64 48   # paste as SESSION_SECRET
make up                   # docker compose up -d --wait
```

- The compose stack runs the backend with `APP_ENV=production` regardless of `.env` (whose `APP_ENV=development` only affects `make dev`). Production refuses to start while `SESSION_SECRET` or `POSTGRES_PASSWORD` is still `change-me`, `SESSION_SECRET` is under 32 characters, or `SESSION_COOKIE_SECURE=false`. Container dev uses `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`, which switches back to development.
- Migrations run automatically: the one-shot `migrate` service runs `alembic upgrade head` after `db` is healthy, and `backend` starts only if it exits 0. A failed migration shows as `migrate exited (1)` in `docker compose ps -a`; read it with `docker compose logs migrate`, fix, then `make up` again. Re-run by hand with `docker compose run --rm migrate`.
- Caddy rejects request bodies over 11 MB on `/api/*` and 6 MB on `/api/v1/chat/transcribe` with 413 before they reach the backend, which enforces its own slightly lower caps.
