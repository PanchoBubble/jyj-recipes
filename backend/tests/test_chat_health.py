from pathlib import Path

from fastapi.testclient import TestClient

from jyj.api.auth import current_user
from jyj.chat.provider import CodexProvider, ProviderHealth
from jyj.chat.router import get_codex_provider
from jyj.main import create_app
from jyj.stt import SttHealth, Transcriber, get_transcriber


class StubTranscriber(Transcriber):
    def __init__(self) -> None:
        super().__init__(whisper_binary="whisper-cli", model_path=Path("ggml-tiny.bin"))

    def health(self) -> SttHealth:
        return SttHealth(available=True, whisper=True, ffmpeg=True, model=True)


STT_READY = {"available": True, "detail": "ready", "model": "ggml-tiny.bin"}


class StubProvider(CodexProvider):
    def __init__(self, health: ProviderHealth) -> None:
        super().__init__()
        self._stub = health

    def health(self) -> ProviderHealth:
        return self._stub


def client_with(health: ProviderHealth, *, signed_in: bool = True) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_codex_provider] = lambda: StubProvider(health)
    app.dependency_overrides[get_transcriber] = StubTranscriber
    if signed_in:
        app.dependency_overrides[current_user] = lambda: object()
    return TestClient(app, base_url="https://testserver")


def test_chat_health_reports_not_authenticated() -> None:
    response = client_with(ProviderHealth(False, "not_authenticated")).get("/api/v1/chat/health")

    assert response.status_code == 200
    assert response.json() == {
        "codex": {"available": False, "detail": "not_authenticated"},
        "stt": STT_READY,
    }


def test_chat_health_reports_ready() -> None:
    response = client_with(ProviderHealth(True, "ready")).get("/api/v1/chat/health")

    assert response.json() == {
        "codex": {"available": True, "detail": "ready"},
        "stt": STT_READY,
    }


def test_chat_health_requires_a_signed_in_user() -> None:
    client = client_with(ProviderHealth(True, "ready"), signed_in=False)

    response = client.get("/api/v1/chat/health")

    assert response.status_code == 401
