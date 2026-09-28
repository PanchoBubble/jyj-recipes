# Codex CLI setup

The chat assistant shells out to the [Codex CLI](https://github.com/openai/codex) inside the `backend` container. The CLI signs in with a ChatGPT account and keeps its own login in `CODEX_HOME=/var/lib/codex`, which is the `codex-home` named volume mounted only into `backend`.

The app never reads, copies or parses anything under `CODEX_HOME` (including `auth.json`). It only runs the `codex` binary. There are no API keys and nothing Codex-related goes in `.env`.

## One-time login (done by a person)

1. Start the stack: `make up`.
2. Run the device-code login inside the container:

   ```sh
   docker compose exec backend codex login --device-auth
   ```

3. The CLI prints a URL and a one-time code. Open the URL on your own phone or laptop, sign in to ChatGPT and enter the code there. Nobody else, and no script, should do this step for you.
4. Check it worked:

   ```sh
   docker compose exec backend codex login status   # exit 0 and "Logged in ..." when ready
   ```

   `GET /api/v1/chat/health` (requires a signed-in app user) returns `{"codex": {"available": true, "detail": "ready"}}` once the login is in place. Other `detail` values: `not_authenticated` (run step 2), `binary_not_found`, `disabled` (`CODEX_ENABLED=false`), `unavailable:<reason>`. The result is cached for about 30 s.

## Log out

```sh
docker compose exec backend codex logout
```

The login lives only in the `codex-home` volume. `docker compose down` keeps it; `docker compose down -v` (or `docker volume rm jyj_codex-home`) deletes it, and the next chat turn reports `not_authenticated` until someone logs in again.

## Usage limits

Every chat turn is a `codex exec` run against the signed-in ChatGPT plan. It counts against that plan's usage limits and shares the rolling window with any interactive Codex use on the same account.

## How the backend runs it

Each turn runs one process (a global queue of 1):

```
codex exec --json --ephemeral --sandbox read-only --skip-git-repo-check \
  --ignore-user-config --ignore-rules --color never \
  --cd <empty temp dir> --output-schema <schema file> [--model $CODEX_MODEL] -
```

- The prompt goes in on stdin; the JSONL event stream is parsed and the final agent message must be a JSON object matching the schema.
- `--sandbox read-only` in an empty temp dir: model-issued shell commands cannot write anywhere or see app code, photos or the database. The bypass flag is never used.
- `--ignore-user-config` and `--ignore-rules`: any `config.toml` or `.rules` in `CODEX_HOME` is ignored, so no MCP servers, hooks or custom providers can load. Auth still comes from `CODEX_HOME`. No `config.toml` is needed.
- `--ephemeral`: no session transcripts are written to the volume.
- Prompts and model output are only logged at DEBUG.

Settings (env): `CODEX_ENABLED` (default `true`), `CODEX_BINARY` (default `codex`), `CODEX_MODEL` (default: the CLI's own default), `CODEX_TIMEOUT_SECONDS` (default `90`).

## Bumping the CLI

`backend/Dockerfile` installs the static musl build from the official `openai/codex` GitHub release, pinned by version and SHA-256 per architecture (`CODEX_VERSION`, `CODEX_SHA256_ARM64`, `CODEX_SHA256_AMD64`). The comment above that stage has the `gh` command that prints the new digests. After bumping, check:

```sh
docker compose build backend
docker compose run --rm --no-deps backend codex --version
docker compose run --rm --no-deps backend codex exec --help | grep -E 'ignore-user-config|ignore-rules|ephemeral|output-schema'
```
