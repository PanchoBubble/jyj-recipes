"""Runs the real whisper-cli + ffmpeg. Skipped unless both binaries and the model exist.

Point WHISPER_MODEL_PATH (and WHISPER_BINARY if not on PATH) at a local install to enable.
"""

import shutil
from pathlib import Path

import pytest

from jyj.config import Settings
from jyj.stt import Transcriber

FIXTURES = Path(__file__).parent / "fixtures" / "stt"


@pytest.fixture(scope="module")
def transcriber() -> Transcriber:
    settings = Settings()
    if shutil.which(settings.whisper_binary) is None or shutil.which("ffmpeg") is None:
        pytest.skip("whisper-cli or ffmpeg not installed")
    if not settings.whisper_model_path.is_file():
        pytest.skip(f"whisper model not found at {settings.whisper_model_path}")
    return Transcriber.from_settings(settings)


@pytest.mark.parametrize(
    ("filename", "mime"),
    [("speech.webm", "audio/webm;codecs=opus"), ("speech.m4a", "audio/mp4")],
)
def test_transcribes_fixture(transcriber: Transcriber, filename: str, mime: str) -> None:
    result = transcriber.transcribe(
        (FIXTURES / filename).read_bytes(), mime, prompt="tomates, cebolla, lista de compras"
    )

    assert result.language == "es"
    assert "lista" in result.text.lower()
    assert result.confidence is not None and result.confidence > 0.5
    assert 2 < result.audio_seconds < 6
