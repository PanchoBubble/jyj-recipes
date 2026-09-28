# Security review

Checklist for PLAN.md section 8, verified on 2026-09-28 against main at `db59abc` plus this
pass's changes. Each line is **PASS**, **FAIL** or **FOLLOW-UP** (a tracked bd ticket).
Evidence is a `file:line`, a test, or command output recorded below.

Re-run the automated parts with `make test` (see `backend/tests/test_security.py`) and the
manual parts with the commands in [How to re-verify](#how-to-re-verify).

## Summary

| Area | Result |
| --- | --- |
| Network | PASS, one FOLLOW-UP (`jyj-ffk`, default bind address) |
| Auth | PASS |
| Authorization | PASS (enforced by a route-walking test) |
| Chat safety | PASS, one FOLLOW-UP (`jyj-nm8`, P1, Codex env) |
| Uploads | PASS, one FOLLOW-UP (`jyj-0qs`, audio magic bytes) |
| Headers | PASS (checked with curl against a running stack) |
| Secrets | PASS |
| Logging | PASS, one FOLLOW-UP (`jyj-v8i`, app INFO logs not emitted) |
| Dependencies | PASS (0 known vulnerabilities) |
| /media | PASS, decision recorded (see below and `jyj-5r1`) |

## 1. Network

- [x] **PASS** Only `web` publishes ports, bound to `WEB_BIND`. `docker-compose.yml:85-86`.
  `db-test` publishes `127.0.0.1:55432` but only in the `test` profile
  (`docker-compose.yml:175-176`); the dev override publishes Vite on `127.0.0.1:5173` only.
  Running stack:
  ```
  $ docker compose ps --format '{{.Service}}\t{{.Ports}}'
  backend  8000/tcp
  db       5432/tcp
  web      127.0.0.1:18080->80/tcp, 127.0.0.1:18443->443/tcp
  ```
  (`8000/tcp` / `5432/tcp` are container-internal, not host-published.)
- [x] **PASS** `db`, `backend`, `migrate`, `backup` have no `ports:` key (`docker-compose.yml`).
- [ ] **FOLLOW-UP `jyj-ffk`** (P2) `WEB_BIND` defaults to `0.0.0.0` (`docker-compose.yml:85`,
  `.env.example`), i.e. every host interface. PLAN.md wants the LAN interface only; the
  runbook must set it to the Pi's LAN IP, and `make env-check` should refuse `0.0.0.0`.
- [x] **PASS** `/api/readyz` blocked at the proxy: `deploy/Caddyfile:49-50`.
  `curl -sk https://localhost:18443/api/readyz` -> `404`.
- [x] **PASS** No tunnels or port forwards in the repo:
  `grep -rnE "ngrok|cloudflared|tailscale funnel|ssh -R|autossh|frpc|localtunnel|upnp"` -> no
  matches. `JYJ_TLS=tailscale` (`deploy/Caddyfile:15-19`) only fetches a certificate from a
  local tailscaled; it does not expose anything.
- [x] **PASS** Interactive API docs (`/docs`, `/redoc`, `/openapi.json`) are only mounted
  when `APP_ENV=development` (`backend/src/jyj/main.py:34-40`, new in this pass). Through
  Caddy those paths already fell through to the SPA; now the backend does not serve them
  either. Test: `test_interactive_docs_are_not_served_outside_development`.

## 2. Authentication

- [x] **PASS** argon2id with argon2-cffi's RFC 9106 low-memory profile:
  `$argon2id$v=19$m=65536,t=3,p=4`, 16-byte salt, 32-byte hash
  (`backend/src/jyj/services/passwords.py:9`). Rehash on login when parameters change
  (`services/auth.py:51-53`, `test_rehashes_when_parameters_change`).
- [x] **PASS** Session tokens are 256-bit `secrets.token_urlsafe(32)` and only the SHA-256
  is stored (`services/auth.py:36-37,60-63`, `test_token_is_hashed_at_rest`).
- [x] **PASS** Cookie `jyj_session`: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`,
  `Max-Age` 30 days (`api/auth.py:37-46`, `test_login_success_sets_secure_cookie_and_me_works`).
  `SESSION_COOKIE_SECURE=false` is refused in production (`config.py:62-63`).
- [x] **PASS** TTL 30 days, sliding, refreshed at most hourly (`services/auth.py:13-14,84-89`,
  `test_session_slides_at_most_hourly`).
- [x] **PASS** Logout deletes the session row and clears the cookie (`api/auth.py:119-124`,
  `test_logout_revokes_session_and_clears_cookie`); reset-password and disable revoke all of a
  user's sessions (`services/auth.py:131-146`).
- [x] **PASS** Rate limit: 5 attempts/min per client IP plus exponential per-username backoff
  (`services/rate_limit.py:15-74`). The IP is the real client: uvicorn runs with
  `--proxy-headers` (`backend/Dockerfile:112`) and trusts only the pinned web container
  (`FORWARDED_ALLOW_IPS`, `docker-compose.yml:57,95`); Caddy has no `trusted_proxies`, so it
  replaces any client-sent `X-Forwarded-For` (`deploy/Caddyfile:75`). Tests:
  `test_rate_limit_keys_on_client_ip_resolved_from_trusted_proxy_only`. Live stack:
  ```
  $ # 6 logins, each with a different spoofed X-Forwarded-For
  401 401 401 401 401 429
  ```
- [x] **PASS** Generic errors: one message for unknown user, wrong password and disabled
  account (`api/auth.py:19,111`), plus a dummy argon2 verify for unknown users
  (`services/passwords.py:32-34`). Tests: `test_bad_credentials_get_the_same_generic_error`,
  `test_unknown_user_still_runs_a_password_hash`, `test_validation_errors_never_echo_the_password`.
- [x] **PASS** No signup: the only unauthenticated write routes are login and logout
  (`PUBLIC` in `backend/tests/test_security.py`).
- [x] **PASS** CLI passwords only via `getpass`, never argv (`backend/src/jyj/cli.py:20-25`,
  `test_password_is_not_accepted_as_argument`).
- [x] **PASS** CSRF: every non-GET/HEAD/OPTIONS request under `/api/v1` needs
  `X-Requested-With: jyj` (`api/csrf.py:21-30`); no CORS middleware is installed, so no
  cross-origin preflight can succeed. `test_every_mutating_route_requires_the_csrf_header`
  enumerates the app's routes (32 mutating routes today) and asserts 403 both without the
  header and with a wrong value, while logged in.

## 3. Authorization

- [x] **PASS** Every route except the `PUBLIC` set (`/healthz`, `/api/healthz`, `/readyz`,
  `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`) requires `current_user`. Two
  checks in `backend/tests/test_security.py` walk `app.routes` (47 protected routes today):
  - `test_every_non_public_route_depends_on_current_user` inspects each route's dependency tree;
  - `test_every_non_public_route_rejects_anonymous_requests` calls each route without a cookie
    and expects a 401 problem response.
  A new route that forgets auth fails both.
- [x] **PASS** Chat is scoped to the owner: conversations filter on `user_id`
  (`chat/conversations.py:26-38`), actions are joined through their conversation
  (`chat/conversations.py:82-91`), confirm/reject go through `get_owned_action`
  (`chat/orchestrator.py` `decide`). Tests: `test_conversations_are_private`,
  `test_only_the_owner_can_confirm_or_reject`.
- Note: recipes, stock, calendar and shopping lists are household-shared by design (PLAN §2).

## 4. Chat safety (read-only review of `backend/src/jyj/chat`)

- [x] **PASS** Model output only reaches data through the registry: the orchestrator parses
  the turn against `registry.output_schema()`, unknown tools are rejected, arguments are
  validated by each tool's Pydantic model and every call is audited in `chat_actions`
  (`chat/tools/registry.py:201-225`, module docstring `chat/orchestrator.py:1-15`).
- [x] **PASS** Tools have no raw SQL, filesystem or network access: the only imports under
  `chat/tools/` are pydantic, `sqlalchemy.select` (registry lookups of `chat_actions`) and
  `jyj.services.*`; no `text(`, `open(`, `Path(`, `subprocess`, `socket`, `urllib` or `httpx`.
- [x] **PASS** Prompt data blocks are escaped: everything but the current request is JSON with
  `<`, `>`, `&` as `\u` escapes inside `<data>` blocks (`chat/orchestrator.py:499-506`), and
  the request itself is escaped too (`chat/orchestrator.py:495`). Tests:
  `test_injection_in_recipe_text_is_quoted_data_and_triggers_nothing`,
  `test_injection_in_user_text_cannot_break_out_of_the_request`.
- [x] **PASS** `codex exec` argv: `--sandbox read-only`, `--ignore-user-config`,
  `--ignore-rules`, `--ephemeral`, empty temp `--cd`, never a bypass/full-auto flag
  (`chat/provider.py:251-272`). Tests: `test_argv_is_locked_down_and_schema_is_written_to_the_run_dir`,
  `test_codex_argv_is_always_read_only_and_ignores_user_config`.
- [x] **PASS** The app never reads `CODEX_HOME` contents: `CODEX_HOME` appears only in the
  compose env and provider docstrings; no reference to `auth.json` or a codex-home path in
  `backend/src` (`test_app_code_never_opens_codex_home`).
- [ ] **FOLLOW-UP `jyj-nm8`** (P1) The Codex subprocess inherits the full backend environment
  (`chat/provider.py:430-433`), including `DATABASE_URL` (with the DB password) and
  `SESSION_SECRET`. The read-only sandbox still allows reading env, so a prompt injection could
  surface them in a reply. Needs an allowlisted env; owned by the chat hardening work.
- [x] **PASS** Tool surface (`build_registry()`): reads `search_recipes`, `get_recipe`,
  `list_ingredients`, `get_stock`, `get_plan`, `preview_shopping`; writes `create_recipe`,
  `update_recipe`, `create_ingredient`, `adjust_stock`, `plan_meal`, `move_meal`,
  `set_servings`, `mark_cooked`, `uncook_meal`, `create_shopping_list`; confirmation required
  for `delete_recipe`, `set_stock`, `remove_meal`. No tool completes a shopping list
  (that is `POST /shopping-lists/{id}/complete`, UI only) and the app holds no money-like state.

## 5. Uploads

- [x] **PASS** Size caps at Caddy and backend. Caddy refuses a declared body >= 6 MB on
  `/api/v1/chat/transcribe` and >= 11 MB elsewhere, and caps streamed bodies
  (`deploy/Caddyfile:52-74`). The backend caps photos at `PHOTO_MAX_BYTES` + 256 KiB and
  transcribe at `STT_MAX_BYTES` + 64 KiB (`backend/src/jyj/main.py`, `api/body_limit.py`), and
  re-checks while reading (`services/photos.py:90`, `chat/voice.py:154-160`). Live stack:
  ```
  7 000 000-byte POST /api/v1/chat/transcribe -> 413 problem+json
  12 000 000-byte PUT /api/v1/recipes/1/photo -> 413 problem+json
  ```
- [x] **PASS** Photo magic-byte sniffing (JPEG, PNG, WebP, HEIF brands) before Pillow, and
  Pillow is limited to the sniffed format (`services/photos.py:99-125`);
  50 MP pixel cap (`services/photos.py:31-33`). Tests: `test_spoofed_or_broken_files_are_rejected`,
  `test_decompression_bomb_dimensions_are_rejected`.
- [x] **PASS** EXIF removed: images are re-encoded from a fresh pixel-only copy, orientation
  applied first (`services/photos.py:147-165`, `test_exif_gps_is_removed_and_orientation_applied`).
- [x] **PASS** Random names: `secrets.token_hex(16)` + `.webp` (`services/photos.py:172`),
  atomic write with temp-file cleanup on failure (`services/photos.py:178-189`).
- [x] **PASS** Audio is never persisted: it is read into memory, written to a per-request
  `TemporaryDirectory` for ffmpeg, the source is unlinked right after conversion and the
  directory is removed on exit (`stt/transcriber.py:140-150`). Tests:
  `test_too_long_audio_is_rejected_and_cleaned_up`, `test_whisper_timeout_kills_and_cleans_up`.
- [ ] **FOLLOW-UP `jyj-0qs`** (P3) Audio is accepted on the client-declared MIME type; the
  real gate is a forced ffmpeg demuxer in a minimal static build (no network protocols,
  `backend/Dockerfile:14-27`). Add a magic-byte check as defence in depth.

## 6. Headers

- [x] **PASS** Verified against a running stack (images built from this tree; the Caddyfile in
  `jyj-web` matches `deploy/Caddyfile` byte for byte):
  ```
  $ curl -skI https://localhost:18443/
  HTTP/2 200
  content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
  permissions-policy: microphone=(self), camera=(self)
  referrer-policy: same-origin
  x-content-type-options: nosniff
  ```
  No `Server` header (`-Server`, `deploy/Caddyfile:34`) and no HSTS (tls internal, per PLAN).
  The same headers are on `/api/*` responses. `style-src 'unsafe-inline'` is needed by Radix;
  scripts stay `'self'` only.

## 7. Secrets

- [x] **PASS** `.env` and `.env.*` are git-ignored except `.env.example`
  (`.gitignore:2-4`; `git check-ignore -v .env` -> `.gitignore:2:.env`). No `.env`, key,
  certificate or `auth.json` was ever committed:
  `git log --all --name-only --format= | sort -u | grep -E '\.env|\.pem$|\.key$|auth\.json|caddy-root'`
  -> `.env.example` only.
- [x] **PASS** Full-history scan:
  ```
  $ docker run --rm -v <repo>:/repo:ro zricethezav/gitleaks:latest git /repo --log-opts=--all --redact
  gitleaks v8.30.1: 44 commits scanned, ~1.95 MB, no leaks found
  ```
- [x] **PASS** `.env.example` has placeholders only (`change-me`), and production refuses to
  start with them or with a short session secret (`backend/src/jyj/config.py:46-67`).
- [x] **PASS** `codex-home` and `caddy-data` are never backed up: the backup service mounts
  only `photos:ro` and `BACKUP_DIR` (`docker-compose.yml:157-159`, comment at `:138`); the `chat-purge` service mounts no volumes.
  Neither path is logged anywhere in `backend/src` or `deploy/`.

## 8. Logging

- [x] **PASS** Nothing sensitive at INFO: auth logs are `login ok user_id=N`, `login failed`,
  `login throttled` (`api/auth.py:101,110,115`); prompts, answers and transcripts are DEBUG only
  (`chat/provider.py:293,384`, `stt/transcriber.py:165`); settings errors hide inputs
  (`config.py:18`). `DB_ECHO=true`, which would log SQL parameters (password and token hashes,
  chat text) at INFO, is now refused in production (`config.py:64-66`, new in this pass,
  `test_sql_echo_is_refused_in_production`).
- [x] **PASS** `test_login_and_chat_turn_log_no_secrets_at_info` captures every log record at
  INFO and above during a good login, a bad login, a chat turn with the fake provider, a
  transcription and logout, and asserts the password, cookie token, token hash, prompt, reply
  and transcript never appear. Live stack: after 6 failed logins,
  `docker logs backend | grep -ciE "nope|jyj_session|set-cookie"` -> `0`.
- [ ] **FOLLOW-UP `jyj-v8i`** (P3) `jyj.*` INFO records have no handler in the running stack
  (only uvicorn access lines appear), so failed logins are not auditable. Not a leak.

## 9. Dependencies

- [x] **PASS** Python, all locked packages including dev:
  ```
  $ uv export --locked --no-hashes --no-emit-project > req.txt && uvx pip-audit -r req.txt --no-deps --disable-pip
  No known vulnerabilities found   (pip-audit 2.10.1)
  ```
- [x] **PASS** Frontend:
  ```
  $ npm audit --omit=dev
  found 0 vulnerabilities
  $ npm audit
  found 0 vulnerabilities
  ```
- Note: the dev dependency `httpx2` is the client Starlette's TestClient imports first
  (`starlette/testclient.py:33`), from PyPI with locked hashes.

## 10. /media is unauthenticated (`jyj-5r1`)

**Decision: accepted as-is.** Caddy serves `/media/*` without a session check
(`deploy/Caddyfile:79-83`). Photo names are 128-bit random and change on every upload
(`services/photos.py:172`), there is no directory listing (`curl -skI .../media/` -> `404`),
the stack is LAN-only, and recipe photos are household-shared data with no per-user privacy.
`forward_auth` would cost a backend round trip per thumbnail on the Pi for no real gain.
Revisit if the app is ever reachable beyond the LAN. `jyj-5r1` now tracks only the orphan-file
sweeper.

## How to re-verify

```sh
make test                                   # includes backend/tests/test_security.py
docker run --rm -v "$PWD:/repo:ro" zricethezav/gitleaks:latest git /repo --log-opts=--all --redact
(cd backend && uv export --locked --no-hashes --no-emit-project > /tmp/req.txt \
  && uvx pip-audit -r /tmp/req.txt --no-deps --disable-pip)
(cd frontend && npm audit --omit=dev)
make up
curl -skI https://localhost/ | grep -iE 'content-security|permissions|nosniff|referrer'
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost/api/readyz   # 404
docker compose ps --format '{{.Service}}\t{{.Ports}}'                   # only web publishes
```

The headers and network checks above ran on a throwaway compose project
(`-p jyjsec`, `WEB_BIND=127.0.0.1`, ports 18080/18443, subnet 10.203.88.0/24, Codex
disabled) to avoid colliding with the real stack; see `jyj-9al` for making that easier.
