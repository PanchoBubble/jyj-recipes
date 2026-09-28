# jyj-recipes plan

Self-hosted household recipes, meal calendar, shopping planner, stock tracker and chat/voice assistant. Runs on a Raspberry Pi via docker compose, LAN only.

Status: planning. Tickets live in beads (`bd ready`, prefix `jyj`). This doc is the source of truth for architecture decisions; tickets reference its sections.

## 0. Open questions

These change scope or defaults. Each has a working assumption so work can start; tickets that depend on one say so.

| # | Question | Working assumption | Blocks |
|---|----------|--------------------|--------|
| Q1 | Which Pi and what else runs on it? (Pancho Pi 5 16 GiB already runs Immich + miso.) | Pi 5, 4+ GiB free, arm64. Memory budget in §7 targets ≤ 1 GiB total. | Deploy, whisper model choice |
| Q2 | HTTPS on the LAN: Caddy internal CA (install root cert on every phone), an owned domain with DNS-01 certs pointing at a private IP (no device installs), or Tailscale HTTPS? | Caddy `tls internal` + hostname, root CA installed on devices. | Voice (mic needs a secure context) |
| Q3 | Which phones/browsers? (iOS Safari, Android Chrome, Firefox?) | iOS Safari + Android Chrome. Firefox Android does not trust user CAs by default. | TLS guide, recorder MIME types |
| Q4 | How many household members, separate logins or one shared account, any roles? | 2 to 4 named accounts, all equal (no roles). | Auth |
| Q5 | UI and voice language(s)? English, Spanish, mixed like miso? | UI in English; voice auto-detect en/es. | Whisper model, prompts |
| Q6 | Units: metric only, or imperial too? Convert across dimensions (e.g. "2 eggs" vs "120 g eggs") via per-ingredient density/piece weight? | Metric + count + a few kitchen units (tsp, tbsp, cup). Cross-dimension only when the ingredient has a conversion set; otherwise listed separately. | Units module, shopping |
| Q7 | Stock deduction: only on explicit "cooked" tap, or automatically once the meal date passes? Negative stock allowed? | Explicit "cooked" tap (undoable). Stock floors at 0, the shortfall is recorded in the movement. | Cook flow |
| Q8 | Shopping range: should meals planned *before* the range (not yet cooked) reserve stock first? Pack sizes / rounding? Store aisles? | v1: "from today" semantics, uncooked meals from today up to range start reserve stock. No pack sizes, optional ingredient category for grouping. | Shopping aggregation |
| Q9 | Chat writes: confirm every write, or only destructive/bulk ones? | Reads + single creates/updates run directly; deletes, bulk (>5 rows) and stock overwrites need a tap to confirm. | Chat orchestrator |
| Q10 | Codex: run the CLI inside the backend container (recommended) or on the Pi host? Which ChatGPT plan/model? Acceptable reply latency? | In-container, default model, target < 20 s per chat turn. Subscription usage shares the plan's rolling limit with interactive Codex use. | Chat provider |
| Q11 | Default meal slots and default servings (household size)? | Slots lunch, dinner, tea; default servings 2. | Seeds |
| Q12 | Chat/audio retention? | Keep chat text 90 days; delete audio right after transcription. | Chat storage |

## 1. Architecture

```
 phone / laptop (LAN)
        │  HTTPS (Caddy, tls internal or DNS-01)
        ▼
 ┌─────────────── docker compose on Pi ───────────────┐
 │ web (Caddy)                                          │
 │   /            → static React build (no Node at run) │
 │   /api/*       → backend:8000                        │
 │   /media/*     → photos volume (read-only)           │
 │                                                      │
 │ backend (FastAPI, 1 uvicorn worker)                  │
 │   REST API ── service layer ── SQLAlchemy ── db      │
 │   chat orchestrator → codex exec (subprocess)        │
 │   transcription     → ffmpeg + whisper.cpp (subproc) │
 │   volumes: photos, codex-home (CLI's own auth)       │
 │                                                      │
 │ db (postgres:16-alpine, tuned for low memory)        │
 └──────────────────────────────────────────────────────┘
```

Key choices:

- **3 containers** as requested: `db`, `backend`, `web`. The "frontend" container is Caddy serving the prebuilt static bundle and reverse-proxying `/api`. No Node process in production saves ~100–200 MiB.
- **Service layer is the only path to data.** REST handlers and chat tools both call the same service functions with the acting user. The model never sees SQL, a DB connection, or the host filesystem.
- **Single backend worker.** Household load is tiny; one worker keeps memory flat. Codex and whisper run as short-lived subprocesses with a concurrency limit of 1 each (queue, not fan out) so the Pi never runs two heavy jobs at once.
- All images multi-arch or built for `linux/arm64`. Dev on macOS (arm64) matches.

## 2. Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async not needed, sync + threadpool), Alembic, uv | Same language as miso, so the Codex/whisper subprocess code ports directly. Pydantic models double as chat tool JSON schemas. |
| DB | PostgreSQL 16 alpine | Requested; `numeric` for quantities, `daterange` queries for calendar. |
| Auth | argon2id password hashes (`argon2-cffi`), server-side sessions in Postgres, HttpOnly+Secure+SameSite=Lax cookie | Simple household auth, revocable sessions, no JWT key management. |
| Frontend | React 19 + TypeScript + Vite, Tailwind v4, shadcn/ui, TanStack Query, React Router | Requested Tailwind + shadcn; Query handles cache invalidation after chat actions. |
| Drag & drop | `@dnd-kit` (PointerSensor + TouchSensor, delay ~200 ms, tolerance ~8 px; `touch-action: none` on drag handles only) | Best-supported touch DnD for React; handle-only `touch-action` keeps the calendar scrollable. |
| Proxy/TLS | Caddy 2 | Automatic `tls internal` CA, or DNS-01 if Q2 picks a domain. Also sets security headers. |
| LLM | Codex CLI `codex exec` using the household's ChatGPT/Codex subscription (miso pattern) | See §5. |
| STT | whisper.cpp (`whisper-cli`) multilingual `ggml-tiny` default, `base` optional; ffmpeg to decode browser audio | What miso benchmarked on a Pi 5: tiny ≈ 0.8 s median with a command-example prompt, base ≈ 1.5–3.7 s. Chat tolerates base if accuracy matters (Q5). |
| Images | Pillow: resize to max 1600 px, strip EXIF, re-encode WebP | Keeps storage small and removes GPS metadata from phone photos. |
| Tests | pytest + a throwaway Postgres (compose `db-test` service), Vitest + Testing Library, Playwright for 2–3 smoke flows | |

## 3. Data model

Quantities are `numeric(12,3)`, stored in the ingredient's **base unit** for its dimension (g, ml, piece). Display units are kept alongside so the UI shows what the user typed.

```
users            id, username (unique, ci), display_name, password_hash, created_at, disabled_at
sessions         id, token_hash (sha256), user_id, created_at, expires_at, last_seen_at, user_agent

units            code PK ('g','kg','ml','l','tsp','tbsp','cup','piece','pinch'), dimension ('mass'|'volume'|'count'|'none'), to_base numeric
ingredients      id, name (unique, ci), dimension, default_unit → units, category (nullable, for shopping grouping),
                 grams_per_ml (nullable), grams_per_piece (nullable), created_at, updated_at

recipes          id, name, description, photo_path (nullable), photo_credit jsonb (nullable: provider, photographer,
                 photographer_url, page_url for photo-search imports), default_servings, created_by → users,
                 created_at, updated_at, archived_at
recipe_ingredients id, recipe_id → recipes (cascade), ingredient_id → ingredients (restrict),
                 amount_per_person numeric, unit_code → units, note, position
                 CHECK amount_per_person > 0 unless unit dimension = 'none' ("to taste")

meal_slots       id, name, position, active            (seed: lunch, dinner, tea; optional labels)
planned_meals    id, date, slot_id? → meal_slots (ON DELETE SET NULL), recipe_id → recipes,
                 servings int > 0, position (dense per date), status ('planned'|'cooked'|'skipped'),
                 cooked_at, cooked_by, created_by, timestamps
                 INDEX (date, position), INDEX (slot_id)

stock_items      ingredient_id PK → ingredients, quantity_base numeric ≥ 0, updated_at
stock_movements  id, ingredient_id, delta_base numeric, reason ('manual'|'cooked'|'purchased'|'correction'|'undo'),
                 shortfall_base numeric default 0, planned_meal_id (nullable), shopping_list_id (nullable),
                 user_id, source ('ui'|'chat'), created_at
                 (append-only ledger; stock_items is the materialised balance, updated in the same transaction)

shopping_lists   id, start_date, end_date, created_by, created_at, status ('open'|'done')
shopping_list_items id, list_id (cascade), ingredient_id, required_base, available_base, to_buy_base,
                 display_unit, checked bool, bought_base (nullable), unconverted jsonb (lines in other dimensions)

chat_conversations id, user_id, created_at, updated_at
chat_messages    id, conversation_id, role ('user'|'assistant'|'tool'), content, input ('text'|'voice'),
                 transcript_confidence (nullable), created_at
chat_actions     id, message_id, tool, arguments jsonb, status ('proposed'|'executed'|'rejected'|'failed'),
                 result jsonb, confirmed_by (nullable), created_at   (audit trail + confirmation queue)
```

Unit rules (module `units.py`, pure, heavily unit-tested):

- Convert within a dimension via `to_base`.
- Cross-dimension only through `grams_per_ml` / `grams_per_piece` on the ingredient.
- Anything unconvertible is kept as a separate line (never silently dropped or guessed).
- `none`-dimension units ("to taste", "pinch") never reach the shopping list quantity, only a "have it?" line.

## 4. API (REST, `/api/v1`, JSON, cookie session)

All mutating requests require the session cookie plus header `X-Requested-With: jyj` (CSRF defence on top of SameSite). Errors use RFC 9457 problem+json.

```
POST   /auth/login            {username, password}  → sets cookie   (rate limited: 5/min/IP + per user backoff)
POST   /auth/logout
GET    /auth/me

GET    /units
GET    /ingredients?q=        POST /ingredients      GET|PATCH|DELETE /ingredients/{id}   (409 if used by a recipe)

GET    /recipes?q=&page=      POST /recipes          GET|PATCH|DELETE /recipes/{id}
       body includes ingredients[] {ingredient_id | new_ingredient{name,dimension,default_unit}, amount_per_person, unit, note}
PUT    /recipes/{id}/photo    multipart, ≤ 10 MiB, jpeg/png/webp/heic sniffed   DELETE /recipes/{id}/photo
POST   /recipes/{id}/photo/from-search  {provider: "openverse"|"pexels", photo_id}; server re-fetches by id, downloads via the provider's allowlist / SSRF guard
GET    /images/search?q=&page=  photo search proxy: Openverse (keyless) by default, Pexels when PEXELS_API_KEY is set
GET    /images/thumb?provider=openverse&id=  Openverse thumbnail proxy (cached, size-capped)
GET    /recipes/{id}/scaled?servings=N

GET    /meal-slots            POST /meal-slots       PATCH|DELETE /meal-slots/{id}   PUT /meal-slots/order
GET    /planned-meals?from=&to=
POST   /planned-meals         {date, recipe_id, servings?, position?, slot_id?}
PATCH  /planned-meals/{id}    {date?, position?, servings?, status?, slot_id?}   (DnD move; slot_id null clears)
DELETE /planned-meals/{id}
POST   /planned-meals/{id}/cook     → stock movements (idempotent)
POST   /planned-meals/{id}/uncook   → compensating 'undo' movements

GET    /stock                 PATCH /stock/{ingredient_id} {set_to | delta, unit}
GET    /stock/movements?ingredient_id=&from=

POST   /shopping/preview      {from, to}   → computed, not stored
POST   /shopping-lists        {from, to}   → snapshot
GET    /shopping-lists        GET|DELETE /shopping-lists/{id}
PATCH  /shopping-lists/{id}/items/{item_id}  {checked?, bought_quantity?, unit?}
POST   /shopping-lists/{id}/complete        → 'purchased' stock movements for checked items

POST   /chat/conversations    GET /chat/conversations/{id}
POST   /chat/conversations/{id}/messages     {text}  → SSE stream: status, assistant text, proposed/executed actions
POST   /chat/actions/{id}/confirm | /reject
POST   /chat/transcribe       multipart audio (≤ 60 s, ≤ 5 MiB) → {text, language, confidence}
GET    /chat/health           → codex + whisper availability

GET    /healthz               (unauthenticated, no detail)
```

## 5. Chat assistant (Codex pattern from miso)

What miso does (`home-assistant/src/miso/providers/codex.py`): shell out to `codex exec --json --sandbox read-only --skip-git-repo-check --cd <empty tempdir> -`, pipe the flattened prompt on stdin, parse the JSONL event stream, enforce a timeout and cancellation, and check readiness with `codex login status`. **The CLI owns its credentials; miso never reads, copies or reuses the stored OAuth token.** Tool execution in miso is done by miso itself after a constrained-JSON pick (`toolpick.py`), validated against an allowlisted schema before anything runs.

jyj-recipes keeps both properties and adds structured output:

1. **Auth setup (one-time, done by a person):** `docker compose exec backend codex login --device-auth`, the user completes the device code flow on their own phone/laptop. `CODEX_HOME` points at the `codex-home` named volume, mounted only into `backend`. The app never reads `auth.json`; it only runs the binary. No API keys, no token copying, nothing in `.env`.
2. **Per turn:** backend builds a prompt = system rules + tool catalog + compact household context (slot names, today's date, recipe/ingredient names with ids, relevant stock) + recent conversation. It runs `codex exec --json --ephemeral --sandbox read-only --skip-git-repo-check --cd <tmp> --output-schema <schema.json> -`.
3. **Output schema** (strict JSON Schema, `additionalProperties: false`): `{ "reply": string, "actions": [ oneOf per-tool {tool, args} ], "needs": [ oneOf read-tool calls ] }`. Reads requested in `needs` are executed and fed back for one more round (max 3 rounds). Writes in `actions` go through the tool registry.
4. **Tool registry:** each tool = name, Pydantic args model (→ JSON Schema), handler calling the service layer as the logged-in user, `requires_confirmation` flag. Unknown tools, invalid args or out-of-scope ids are rejected before any handler runs. Tool results are passed back to the model as quoted data, never as instructions.
5. **Confirmation:** per Q9, destructive/bulk actions are stored as `proposed` and rendered as cards with Confirm/Reject; everything else executes and shows as an "undo-able" card.
6. **Codex agent sandbox:** read-only sandbox in an empty temp dir, no MCP servers configured in `CODEX_HOME/config.toml`, so even if the model tries to run shell commands it can't touch the DB, photos or app code. Never pass `--dangerously-bypass-approvals-and-sandbox`.
7. **Limits:** one Codex process at a time (queue), 90 s timeout, cancel on client disconnect, `/chat/health` surfaces `not_authenticated` so the UI can show "someone needs to run codex login".

Initial tool set: `search_recipes, get_recipe, create_recipe, update_recipe, delete_recipe*, list_ingredients, create_ingredient, get_stock, adjust_stock, set_stock*, get_plan, plan_meal, move_meal, remove_meal*, mark_cooked, preview_shopping, create_shopping_list` (* = confirmation).

Risks: each `codex exec` spawn costs seconds (session start + reasoning); a 3-round turn could hit 20–40 s (unmeasured, jyj-chat benchmark ticket measures it). Mitigation: preload context so most turns are one round; stream a status line. Subscription runs share the plan's rolling usage window with interactive Codex use.

## 6. Voice

- **Secure context:** `getUserMedia` only works on HTTPS or localhost, so voice depends on §6.1. The UI checks `window.isSecureContext` and explains what's missing instead of failing silently.
- **Recording:** `MediaRecorder`, pick the first supported of `audio/webm;codecs=opus` (Chrome/Firefox) then `audio/mp4` (Safari). Hold-to-talk or tap toggle, 60 s cap, level meter.
- **Transcription:** backend writes the upload to a temp file, `ffmpeg -ar 16000 -ac 1` to WAV, runs `whisper-cli --model ggml-tiny.bin -l auto -oj` (miso's pinned approach) with a short domain prompt (recipe/ingredient names), deletes both files. One job at a time.
- **Flow:** transcript appears in the input box, editable, then sent through the exact same chat pipeline (`input='voice'`). Low-confidence transcripts are never auto-sent.
- Model choice: tiny by default (≈ 75 MiB, sub-second on Pi 5 per miso benchmark). If Spanish accuracy matters more than 1–2 s, switch to base (config only).

### 6.1 LAN HTTPS options (Q2)

| Option | Device setup | Notes |
|--------|--------------|-------|
| A. Caddy `tls internal` + hostname (e.g. `recipes.home.arpa` via router DNS or `/etc/hosts`, or `<pi>.local` mDNS) | Install Caddy root CA once per device (iOS: profile + enable full trust; Android: Settings → Encryption & credentials → CA certificate; Chrome honours it, Firefox needs `security.enterprise_roots`) | Fully offline. Default plan. |
| B. Owned domain, DNS-01 via Caddy DNS plugin, A record → private LAN IP | None | Needs a domain + DNS API token in `.env`. Nothing exposed publicly, but the hostname appears in CT logs. |
| C. Tailscale HTTPS (`*.ts.net`) | Tailscale app on each device | Also gives remote access later. |

## 7. Pi constraints

Memory budget target (steady state): Postgres ≤ 256 MiB (`shared_buffers=64MB`, `work_mem=4MB`, `max_connections=20`), backend ≤ 250 MiB idle, Caddy ≤ 40 MiB. Transient: whisper tiny ~150 MiB, Codex CLI ~100 MiB (unmeasured, verify on Pi). Compose sets `mem_limit` per service and healthchecks. Data on the Pi's external disk (bind path from `.env`), nightly `pg_dump` + photos rsync.

## 8. Security

- LAN only: Caddy binds to the LAN interface; `db` and `backend` publish no ports. No port forwarding, no tunnel.
- Household auth (§2), session TTL 30 days sliding, logout revokes, `create-user` / `reset-password` via `docker compose exec backend jyj users ...` (no signup endpoint).
- Login rate limiting and generic error messages.
- Headers: CSP (`default-src 'self'`, `media-src 'self' blob:`, `img-src` adds `https://images.pexels.com` for Pexels thumbnails; Openverse thumbnails are proxied and stay `'self'`), `Permissions-Policy: microphone=(self)`, HSTS off for tls internal (avoid lock-in), `X-Content-Type-Options`, `frame-ancestors 'none'`.
- Uploads: size caps, magic-byte sniffing, re-encode images, random filenames, audio never persisted.
- Chat: tools scoped to the service layer, schema validation, confirmation for destructive ops, audit table, no raw SQL, Codex sandbox read-only in empty dir.
- Secrets: `.env` (DB password, session secret) git-ignored, `.env.example` committed with placeholders; Codex auth lives only in its own volume, never in the repo or `.env`.
- Logs: no passwords, cookies, transcripts or prompts at INFO; request ids only.

## 9. Milestones

| M | Goal | Done when |
|---|------|-----------|
| M0 Foundations | Scaffold, compose on arm64, HTTPS, auth, app shell | Log in from a phone over HTTPS on the LAN |
| M1 Ingredients & stock | Units, ingredients, stock ledger + UI | Adjust stock from the phone |
| M2 Recipes | Recipe CRUD with per-person ingredients + photo | Create a recipe with photo on mobile |
| M3 Meal calendar | Slots, planned meals, touch DnD, cook → stock | Drag a recipe onto Thursday dinner by touch, mark cooked, stock drops |
| M4 Shopping | Aggregation, lists, bought → stock | Shopping list for next week matches a hand calculation |
| M5 Chat | Codex provider, tools, orchestrator, UI | "Plan pasta for dinner Friday for 3 and add 1 kg flour to stock" works end to end |
| M6 Voice | whisper.cpp, recorder, voice → chat | Same request spoken on a phone |
| M7 Ops & hardening | Backups, Pi runbook, security pass | Restore drill passes, checklist in §8 verified |

M1–M4 are the usable app without AI; M5/M6 can slip without blocking daily use.

## 10. Repo layout (target)

```
backend/   pyproject.toml, src/jyj/{api,services,models,tools,chat,stt,units.py,cli.py}, alembic/, tests/
frontend/  package.json, src/{routes,components/ui (shadcn),features/{recipes,calendar,shopping,stock,chat}}, tests/
deploy/    Caddyfile, postgres.conf, backup/
docs/      PLAN.md, TLS.md, PI.md
docker-compose.yml, docker-compose.dev.yml, .env.example, Makefile
```

## Sources

- miso reference: `home-assistant/src/miso/providers/codex.py`, `toolpick.py`, `transcription.py`, `docs/miso-transcription-benchmark.md` (read-only)
- dnd-kit touch/pointer sensors: https://dndkit.com/react/guides/sensors/ , https://dndkit.com/extend/sensors/pointer-sensor/
- Caddy internal TLS: https://caddyserver.com/docs/automatic-https , https://caddyserver.com/docs/caddyfile/directives/tls
- getUserMedia secure context: https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
- Codex auth/non-interactive: https://developers.openai.com/codex/auth , https://learn.chatgpt.com/docs/non-interactive-mode
