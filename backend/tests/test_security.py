"""Cross-cutting security invariants, checked against every route the app actually serves.

New routes are picked up automatically: a route that is not in PUBLIC must require a
logged-in user, and every state-changing /api/v1 route must demand the CSRF header.
"""

import logging
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fake_chat import FakeProvider, turn
from fastapi.dependencies.models import Dependant
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import Connection
from sqlalchemy.orm import Session, sessionmaker

from jyj.api.auth import SESSION_COOKIE, current_user
from jyj.chat.provider import CodexProvider
from jyj.chat.router import get_chat_session_factory, get_codex_provider
from jyj.chat.voice import TRANSCRIBE_PATH
from jyj.db import get_db, session_scope
from jyj.main import API_PREFIX, create_app
from jyj.models import User
from jyj.services import auth as auth_service
from jyj.stt import Transcriber, TranscriptionResult, get_transcriber

PASSWORD = "correct horse battery staple"
CSRF = {"X-Requested-With": "jyj"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SRC = Path(__file__).resolve().parent.parent / "src" / "jyj"

# (method, path) pairs reachable without a session, each with the reason it is safe.
PUBLIC = {
    ("GET", "/healthz"): "liveness probe, static body",
    ("GET", "/api/healthz"): "liveness probe through the proxy, static body",
    ("GET", "/readyz"): "compose healthcheck; Caddy answers 404 for /api/readyz",
    ("POST", API_PREFIX + "/auth/login"): "the login itself",
    ("POST", API_PREFIX + "/auth/logout"): "only revokes the caller's own cookie, if any",
}


def _routes() -> list[tuple[str, str, Dependant | None]]:
    found = []
    for route in iter_route_contexts(create_app().routes):
        dependant = getattr(route, "dependant", None)
        for method in sorted(route.methods or ()):
            if method != "HEAD":
                found.append((method, route.path, dependant))
    return found


ROUTES = _routes()
PROTECTED = [(m, p) for m, p, _ in ROUTES if (m, p) not in PUBLIC]
MUTATING = [(m, p) for m, p, _ in ROUTES if m not in SAFE_METHODS and p.startswith(API_PREFIX)]


def _calls(dependant: Dependant) -> set[object]:
    found = {dependant.call}
    for sub in dependant.dependencies:
        found |= _calls(sub)
    return found


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def _ids(pairs: list[tuple[str, str]]) -> list[str]:
    return [f"{m} {p}" for m, p in pairs]


def test_route_inventory_is_what_this_file_expects() -> None:
    paths = {(m, p) for m, p, _ in ROUTES}
    assert set(PUBLIC) <= paths, "a PUBLIC entry no longer exists; drop it"
    assert len(PROTECTED) > 30
    stray = [p for m, p in PROTECTED if not p.startswith(API_PREFIX + "/")]
    assert stray == [], "only the PUBLIC probes may live outside /api/v1"


def test_interactive_docs_are_not_served_outside_development() -> None:
    assert not [p for _, p, _ in ROUTES if p in {"/docs", "/redoc", "/openapi.json"}]


@pytest.mark.parametrize(("method", "path"), PROTECTED, ids=_ids(PROTECTED))
def test_every_non_public_route_depends_on_current_user(method: str, path: str) -> None:
    dependant = next(d for m, p, d in ROUTES if (m, p) == (method, path))
    assert dependant is not None and current_user in _calls(dependant)


@pytest.fixture
def session_factory(db_connection: Connection) -> Callable[[], Session]:
    return sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def db(session_factory: Callable[[], Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


class FakeTranscriber(Transcriber):
    text = "transcript-marker-9d1e"

    def __init__(self) -> None:
        super().__init__(whisper_binary="whisper-cli", model_path=Path("/models/ggml-tiny.bin"))

    def transcribe(self, audio: bytes, mime_type: str, *, prompt: str = "") -> TranscriptionResult:
        return TranscriptionResult(
            text=self.text,
            language="en",
            language_probability=0.99,
            confidence=0.9,
            audio_seconds=1.0,
            elapsed_seconds=0.1,
            model="ggml-tiny.bin",
        )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(session_factory, provider: FakeProvider) -> Iterator[TestClient]:
    app = create_app()

    def test_db() -> Iterator[Session]:
        yield from session_scope(session_factory)

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_codex_provider] = lambda: provider
    app.dependency_overrides[get_chat_session_factory] = lambda: session_factory
    app.dependency_overrides[get_transcriber] = FakeTranscriber
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def alice(db: Session) -> User:
    user = auth_service.create_user(db, "alice", "Alice", PASSWORD)
    db.commit()
    return user


@pytest.mark.parametrize(("method", "path"), PROTECTED, ids=_ids(PROTECTED))
def test_every_non_public_route_rejects_anonymous_requests(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, _url(path), headers=CSRF)
    assert response.status_code == 401, response.text
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize(("method", "path"), MUTATING, ids=_ids(MUTATING))
def test_every_mutating_route_requires_the_csrf_header(
    client: TestClient, db: Session, alice: User, method: str, path: str
) -> None:
    issued = auth_service.create_session(db, alice, "pytest")
    db.commit()
    client.cookies.set(SESSION_COOKIE, issued.token)
    for headers in ({}, {"X-Requested-With": "XMLHttpRequest"}):
        response = client.request(method, _url(path), headers=headers)
        assert response.status_code == 403, response.text
        assert "X-Requested-With" in response.json()["detail"]


def test_login_and_chat_turn_log_no_secrets_at_info(
    client: TestClient, alice: User, provider: FakeProvider, caplog: pytest.LogCaptureFixture
) -> None:
    prompt_marker = "prompt-marker-51c7"
    reply_marker = "reply-marker-a04b"
    provider.steps.append(turn(reply=reply_marker))
    caplog.set_level(logging.INFO)

    login = client.post(
        API_PREFIX + "/auth/login", json={"username": "alice", "password": PASSWORD}, headers=CSRF
    )
    assert login.status_code == 200
    token = login.cookies[SESSION_COOKIE]
    bad = client.post(
        API_PREFIX + "/auth/login",
        json={"username": "alice", "password": PASSWORD + "x"},
        headers=CSRF,
    )
    assert bad.status_code == 401
    conversation = client.post(API_PREFIX + "/chat/conversations", headers=CSRF).json()
    sent = client.post(
        f"{API_PREFIX}/chat/conversations/{conversation['id']}/messages",
        json={"text": prompt_marker},
        headers=CSRF,
    )
    assert sent.status_code == 200 and reply_marker in sent.text
    heard = client.post(
        API_PREFIX + TRANSCRIBE_PATH,
        files={"audio": ("clip.webm", b"\x1a\x45\xdf\xa3" + b"\0" * 64, "audio/webm")},
        headers=CSRF,
    )
    assert heard.status_code == 200 and heard.json()["text"] == FakeTranscriber.text
    client.post(API_PREFIX + "/auth/logout", headers=CSRF)

    assert provider.calls == 1 and prompt_marker in provider.prompts[0]
    logged = "\n".join(
        f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records if r.levelno >= logging.INFO
    )
    assert caplog.records, "expected the auth events to be logged"
    for secret in (PASSWORD, token, auth_service.hash_token(token)):
        assert secret not in logged
    for text in (prompt_marker, reply_marker, FakeTranscriber.text):
        assert text not in logged


def test_codex_argv_is_always_read_only_and_ignores_user_config(tmp_path: Path) -> None:
    for model in (None, "gpt-test"):
        argv = CodexProvider(model=model).build_argv("codex", tmp_path, tmp_path / "s.json")
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--ignore-user-config" in argv and "--ephemeral" in argv
        assert not [a for a in argv if "bypass" in a or "full-auto" in a or "danger" in a]


def test_app_code_never_opens_codex_home() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if re.search(r"auth\.json|codex_home|environ\[?\.?(get\()?[\"']CODEX_HOME", source):
            offenders.append(path.relative_to(SRC))
    assert offenders == []


def test_sql_echo_is_refused_in_production() -> None:
    from pydantic import ValidationError

    from jyj.config import Settings

    with pytest.raises(ValidationError, match="DB_ECHO"):
        Settings(
            app_env="production",
            database_url="postgresql+psycopg://u:p@db/jyj",
            session_secret="s" * 40,
            db_echo=True,
        )
