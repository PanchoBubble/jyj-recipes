import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_chat import FakeProvider, turn
from fastapi.testclient import TestClient
from sqlalchemy import Connection
from sqlalchemy.orm import Session, sessionmaker

from jyj.api.auth import SESSION_COOKIE
from jyj.chat.provider import ProviderHealth
from jyj.chat.router import get_chat_session_factory, get_codex_provider
from jyj.chat.voice import MULTIPART_OVERHEAD_BYTES, UNAVAILABLE_DETAIL, domain_prompt
from jyj.config import get_settings
from jyj.db import get_db, session_scope
from jyj.main import create_app
from jyj.models import Recipe, StockSource, User
from jyj.services import auth as auth_service
from jyj.services import ingredients as ingredients_service
from jyj.services.rate_limit import SlidingWindowLimiter
from jyj.stt import (
    SttBadAudio,
    SttFailed,
    SttHealth,
    SttTimeout,
    SttTooLarge,
    SttTooLong,
    SttUnavailable,
    Transcriber,
    TranscriptionResult,
    get_transcriber,
)
from jyj.stt.transcriber import MAX_PROMPT_CHARS

CSRF = {"X-Requested-With": "jyj"}
API = "/api/v1/chat"
AUDIO = b"\x1aE\xdf\xa3fake-webm"


class FakeTranscriber(Transcriber):
    def __init__(self) -> None:
        super().__init__(whisper_binary="whisper-cli", model_path=Path("/models/ggml-tiny.bin"))
        self.result = TranscriptionResult(
            text="añade harina",
            language="es",
            language_probability=0.97,
            confidence=0.83,
            audio_seconds=2.345,
            elapsed_seconds=0.4,
            model="ggml-tiny.bin",
        )
        self.error: Exception | None = None
        self.state = SttHealth(available=True, whisper=True, ffmpeg=True, model=True)
        self.calls: list[tuple[bytes, str, str]] = []

    def health(self) -> SttHealth:
        return self.state

    def transcribe(self, audio: bytes, mime_type: str, *, prompt: str = "") -> TranscriptionResult:
        self.calls.append((audio, mime_type, prompt))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def session_factory(db_connection: Connection) -> Callable[[], Session]:
    return sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def db(session_factory: Callable[[], Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def stt() -> FakeTranscriber:
    return FakeTranscriber()


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def make_client(session_factory, db: Session, stt: FakeTranscriber, provider: FakeProvider):
    clients: list[TestClient] = []

    def build(user: User | None, *, csrf: bool = True) -> TestClient:
        app = create_app()

        def test_db() -> Iterator[Session]:
            yield from session_scope(session_factory)

        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[get_transcriber] = lambda: stt
        app.dependency_overrides[get_codex_provider] = lambda: provider
        app.dependency_overrides[get_chat_session_factory] = lambda: session_factory
        client = TestClient(app, base_url="https://testserver")
        client.__enter__()
        clients.append(client)
        if csrf:
            client.headers.update(CSRF)
        if user is not None:
            issued = auth_service.create_session(db, user, "pytest")
            db.commit()
            client.cookies.set(SESSION_COOKIE, issued.token)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def user(db: Session) -> User:
    created = auth_service.create_user(db, "cook", "Cook", "correct horse battery")
    db.commit()
    return created


@pytest.fixture
def client(make_client, user: User) -> TestClient:
    return make_client(user)


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    yield monkeypatch
    monkeypatch.undo()
    get_settings.cache_clear()


def upload(client: TestClient, data: bytes = AUDIO, mime: str = "audio/webm;codecs=opus") -> object:
    return client.post(f"{API}/transcribe", files={"audio": ("clip.webm", data, mime)})


# --- happy path ----------------------------------------------------------------------------


def test_transcribes_an_upload(client: TestClient, stt: FakeTranscriber) -> None:
    response = upload(client)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "text": "añade harina",
        "language": "es",
        "confidence": 0.83,
        "low_confidence": False,
        "duration_seconds": 2.35,
    }
    [(audio, mime, _)] = stt.calls
    assert (audio, mime) == (AUDIO, "audio/webm;codecs=opus")


@pytest.mark.parametrize(
    "mime", ["audio/ogg", "audio/mp4", "audio/aac", "audio/x-m4a", "audio/wav", "audio/webm"]
)
def test_accepts_supported_types(client: TestClient, mime: str) -> None:
    assert upload(client, mime=mime).status_code == 200


def test_rejects_unsupported_types(client: TestClient, stt: FakeTranscriber) -> None:
    response = upload(client, mime="application/octet-stream")

    assert response.status_code == 415
    assert response.headers["content-type"] == "application/problem+json"
    assert stt.calls == []


def test_prompt_uses_household_recipe_and_ingredient_names(
    client: TestClient, stt: FakeTranscriber, db: Session, user: User
) -> None:
    ingredients_service.create_ingredient(db, user, StockSource.UI, name="Harina", dimension="mass")
    db.add(Recipe(name="Tortilla de patatas", created_by=user.id))
    db.add(Recipe(name="Receta vieja", created_by=user.id, archived_at=datetime.now(UTC)))
    db.commit()

    upload(client)

    [(_, _, prompt)] = stt.calls
    assert prompt == "Tortilla de patatas, Harina"


def test_prompt_is_capped(db: Session, user: User) -> None:
    for index in range(40):
        db.add(Recipe(name=f"Receta número {index:02d} con nombre largo", created_by=user.id))
    db.flush()

    prompt = domain_prompt(db)

    assert 0 < len(prompt) <= MAX_PROMPT_CHARS
    assert prompt.startswith("Receta número 39")
    assert not prompt.endswith(", ")


# --- low confidence ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "text", "low"),
    [(0.5, "hola", False), (0.49, "hola", True), (None, "hola", True), (0.9, "  ", True)],
)
def test_low_confidence_default_threshold(
    client: TestClient, stt: FakeTranscriber, confidence: float | None, text: str, low: bool
) -> None:
    stt.result = TranscriptionResult(text, "es", None, confidence, 1.0, 0.1, "ggml-tiny.bin")

    assert upload(client).json()["low_confidence"] is low


def test_low_confidence_threshold_is_configurable(
    settings_env: pytest.MonkeyPatch, client: TestClient
) -> None:
    settings_env.setenv("STT_LOW_CONFIDENCE", "0.9")
    get_settings.cache_clear()

    assert upload(client).json()["low_confidence"] is True


# --- errors --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (SttTooLarge("x"), 413, "Recording must be at most 5 MiB"),
        (SttTooLong("x"), 422, "Recording must be at most 60 seconds"),
        (SttBadAudio("x"), 422, "Audio could not be decoded"),
        (SttUnavailable("x"), 503, UNAVAILABLE_DETAIL),
        (SttTimeout("x"), 504, "Transcription timed out"),
        (SttFailed("x"), 502, "Transcription failed"),
    ],
)
def test_stt_errors_map_to_problems(
    client: TestClient, stt: FakeTranscriber, error: Exception, status: int, detail: str
) -> None:
    stt.error = error

    response = upload(client)

    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == detail


def test_unavailable_detail_names_the_download_command() -> None:
    assert "make whisper-download" in UNAVAILABLE_DETAIL


def test_oversized_upload_is_refused_before_transcribing(
    client: TestClient, stt: FakeTranscriber
) -> None:
    stt.max_bytes = 1024

    response = upload(client, b"\0" * 2048)

    assert response.status_code == 413
    assert stt.calls == []


def test_body_limit_refuses_large_requests_up_front(
    client: TestClient, stt: FakeTranscriber
) -> None:
    limit = get_settings().stt_max_bytes + MULTIPART_OVERHEAD_BYTES

    response = upload(client, b"\0" * (limit + 1))

    assert response.status_code == 413
    assert response.json()["detail"] == "Request body is too large"
    assert stt.calls == []


def test_transcript_is_not_logged_above_debug(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        upload(client)

    assert "harina" not in caplog.text


# --- auth, csrf, rate limit ----------------------------------------------------------------


def test_transcribe_requires_a_session(make_client, stt: FakeTranscriber) -> None:
    assert upload(make_client(None)).status_code == 401
    assert stt.calls == []


def test_transcribe_requires_the_csrf_header(make_client, user: User, stt: FakeTranscriber) -> None:
    response = upload(make_client(user, csrf=False))

    assert response.status_code == 403
    assert stt.calls == []


def test_transcribe_is_rate_limited_per_user(
    client: TestClient, make_client, db: Session, stt: FakeTranscriber
) -> None:
    limiter = SlidingWindowLimiter(limit=2, window=60.0)
    client.app.state.transcribe_limiter = limiter
    other = make_client(auth_service.create_user(db, "sous", "Sous", "correct horse battery"))
    other.app.state.transcribe_limiter = limiter

    assert [upload(client).status_code for _ in range(2)] == [200, 200]
    limited = upload(client)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    assert upload(other).status_code == 200
    assert len(stt.calls) == 3


def test_default_rate_limit_is_twenty_per_minute(client: TestClient) -> None:
    assert client.app.state.transcribe_limiter.limit == 20


def test_sliding_window_frees_slots_after_the_window() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=1, window=60.0, clock=lambda: now[0])

    assert limiter.acquire("1") == 0
    assert limiter.acquire("1") == 60.0
    now[0] = 60.0
    assert limiter.acquire("1") == 0


# --- voice messages ------------------------------------------------------------------------


def test_voice_message_is_recorded_through_the_same_pipeline(
    client: TestClient, provider: FakeProvider
) -> None:
    conversation_id = client.post(f"{API}/conversations").json()["id"]
    provider.steps.append(turn("Listo."))

    response = client.post(
        f"{API}/conversations/{conversation_id}/messages",
        json={"text": "añade harina, editado", "input": "voice", "transcript_confidence": 0.41},
    )

    assert response.status_code == 200
    assert "añade harina, editado" in provider.prompts[0]
    [message, reply] = client.get(f"{API}/conversations/{conversation_id}").json()["messages"]
    assert (message["content"], message["input"], message["transcript_confidence"]) == (
        "añade harina, editado",
        "voice",
        0.41,
    )
    assert reply["content"] == "Listo."


@pytest.mark.parametrize("confidence", [-0.1, 1.01, "high"])
def test_voice_confidence_must_be_a_probability(client: TestClient, confidence: object) -> None:
    conversation_id = client.post(f"{API}/conversations").json()["id"]

    response = client.post(
        f"{API}/conversations/{conversation_id}/messages",
        json={"text": "hola", "input": "voice", "transcript_confidence": confidence},
    )

    assert response.status_code == 422


# --- health --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "detail"),
    [
        (SttHealth(True, True, True, True), "ready"),
        (SttHealth(False, False, True, True), "whisper_missing"),
        (SttHealth(False, True, False, True), "ffmpeg_missing"),
        (SttHealth(False, True, True, False), "model_missing"),
    ],
)
def test_chat_health_includes_stt(
    client: TestClient,
    provider: FakeProvider,
    stt: FakeTranscriber,
    state: SttHealth,
    detail: str,
) -> None:
    provider.health = lambda: ProviderHealth(True, "ready")  # type: ignore[attr-defined]
    stt.state = state

    body = client.get(f"{API}/health").json()

    assert body["stt"] == {"available": state.available, "detail": detail, "model": "ggml-tiny.bin"}
