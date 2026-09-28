import asyncio
import json
import os
import stat
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from jyj.config import Settings
from jyj.stt import (
    SttBadAudio,
    SttFailed,
    SttTimeout,
    SttTooLarge,
    SttTooLong,
    SttUnavailable,
    Transcriber,
)
from jyj.stt import download as dl

WHISPER_JSON = {
    "result": {"language": "es"},
    "transcription": [
        {
            "text": " Añade tomates",
            "tokens": [
                {"text": "[_BEG_]", "p": 0.99},
                {"text": " Añade", "p": 0.8},
                {"text": " tomates", "p": 0.6},
                {"text": "[_TT_50]", "p": 0.01},
            ],
        },
        {"text": " a la lista.", "tokens": [{"text": " a la lista.", "p": 1.0}]},
    ],
}

FAKE_FFMPEG = """
import json, os, sys, wave
args = sys.argv[1:]
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg-args.json")
with open(log, "w") as fh:
    json.dump(args, fh)
{body}
with wave.open(args[-1], "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"\\0\\0" * int(16000 * SECONDS))
"""

FAKE_WHISPER = """
import json, os, sys, time
args = sys.argv[1:]
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "whisper-args.json"), "w") as fh:
    json.dump({{"args": args, "env": dict(os.environ)}}, fh)
{body}
prefix = args[args.index("--output-file") + 1]
with open(prefix + ".json", "w") as fh:
    fh.write(PAYLOAD)
sys.stderr.write("whisper_full_with_state: auto-detected language: es (p = 0.93)\\n")
"""


def write_exe(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.write_text(f"#!{sys.executable}\n{textwrap.dedent(source)}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def model(tmp_path: Path) -> Path:
    path = tmp_path / "ggml-tiny.bin"
    path.write_bytes(b"model")
    return path


def make(
    bin_dir: Path,
    work_dir: Path,
    model: Path,
    *,
    seconds: float = 2.0,
    ffmpeg_body: str = "",
    whisper_body: str = "",
    payload: object = WHISPER_JSON,
    **kwargs: object,
) -> Transcriber:
    ffmpeg = write_exe(
        bin_dir, "ffmpeg", FAKE_FFMPEG.format(body=f"SECONDS = {seconds}\n{ffmpeg_body}")
    )
    text = payload if isinstance(payload, str) else json.dumps(payload)
    whisper = write_exe(
        bin_dir,
        "whisper-cli",
        FAKE_WHISPER.format(body=f"PAYLOAD = {text!r}\n{whisper_body}"),
    )
    options: dict[str, object] = {"timeout_seconds": 5.0, **kwargs}
    return Transcriber(
        whisper_binary=str(whisper),
        ffmpeg_binary=str(ffmpeg),
        model_path=model,
        work_dir=work_dir,
        **options,  # type: ignore[arg-type]
    )


def ffmpeg_args(bin_dir: Path) -> list[str]:
    return json.loads((bin_dir / "ffmpeg-args.json").read_text())


def whisper_call(bin_dir: Path) -> dict[str, object]:
    return json.loads((bin_dir / "whisper-args.json").read_text())


def test_transcribes_and_parses_result(bin_dir: Path, work_dir: Path, model: Path) -> None:
    transcriber = make(bin_dir, work_dir, model, threads=3)

    result = transcriber.transcribe(b"audio", "audio/webm;codecs=opus", prompt="tomate\ncebolla")

    assert result.text == "Añade tomates a la lista."
    assert result.language == "es"
    assert result.language_probability == pytest.approx(0.93)
    assert result.confidence == pytest.approx((0.8 + 0.6 + 1.0) / 3)
    assert result.audio_seconds == pytest.approx(2.0)
    assert result.model == "ggml-tiny.bin"
    assert list(work_dir.iterdir()) == []

    args = ffmpeg_args(bin_dir)
    assert args[args.index("-f") + 1] == "matroska"
    assert args[args.index("-protocol_whitelist") + 1] == "file"
    for flag, value in (("-ar", "16000"), ("-ac", "1"), ("-c:a", "pcm_s16le")):
        assert args[args.index(flag) + 1] == value
    assert "-nostdin" in args

    call = whisper_call(bin_dir)
    wargs = call["args"]
    assert isinstance(wargs, list)
    assert wargs[wargs.index("--language") + 1] == "es"
    assert wargs[wargs.index("--threads") + 1] == "3"
    assert wargs[wargs.index("--model") + 1] == str(model)
    assert wargs[wargs.index("--prompt") + 1] == "tomate cebolla"
    assert "--output-json-full" in wargs


def test_children_do_not_inherit_app_secrets(
    bin_dir: Path, work_dir: Path, model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SESSION_SECRET", "s3cret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://jyj:pw@db/jyj")
    monkeypatch.setenv("SECRET_X", "x")
    make(bin_dir, work_dir, model).transcribe(b"audio", "audio/mp4")

    env = whisper_call(bin_dir)["env"]
    assert isinstance(env, dict)
    assert not {"SESSION_SECRET", "DATABASE_URL", "SECRET_X"} & set(env)
    assert env["HOME"] != os.environ.get("HOME")
    assert ffmpeg_args(bin_dir)[ffmpeg_args(bin_dir).index("-f") + 1] == "mov"


def test_no_prompt_flag_when_prompt_empty(bin_dir: Path, work_dir: Path, model: Path) -> None:
    make(bin_dir, work_dir, model).transcribe(b"audio", "audio/ogg", prompt="  ")

    assert "--prompt" not in whisper_call(bin_dir)["args"]  # type: ignore[operator]


def test_rejects_oversized_audio_before_running(bin_dir: Path, work_dir: Path, model: Path) -> None:
    transcriber = make(bin_dir, work_dir, model, max_bytes=10)

    with pytest.raises(SttTooLarge):
        transcriber.transcribe(b"x" * 11, "audio/webm")
    assert not (bin_dir / "ffmpeg-args.json").exists()


@pytest.mark.parametrize("mime", ["text/plain", "application/x-mpegurl", ""])
def test_rejects_unsupported_mime(bin_dir: Path, work_dir: Path, model: Path, mime: str) -> None:
    with pytest.raises(SttBadAudio):
        make(bin_dir, work_dir, model).transcribe(b"audio", mime)
    assert not (bin_dir / "ffmpeg-args.json").exists()


def test_rejects_empty_audio(bin_dir: Path, work_dir: Path, model: Path) -> None:
    with pytest.raises(SttBadAudio):
        make(bin_dir, work_dir, model).transcribe(b"", "audio/webm")


def test_too_long_audio_is_rejected_and_cleaned_up(
    bin_dir: Path, work_dir: Path, model: Path
) -> None:
    transcriber = make(bin_dir, work_dir, model, seconds=61.0, max_seconds=60.0)

    with pytest.raises(SttTooLong):
        transcriber.transcribe(b"audio", "audio/webm")
    assert list(work_dir.iterdir()) == []
    assert not (bin_dir / "whisper-args.json").exists()
    args = ffmpeg_args(bin_dir)
    assert float(args[args.index("-t") + 1]) > 60


def test_small_overshoot_is_accepted(bin_dir: Path, work_dir: Path, model: Path) -> None:
    result = make(bin_dir, work_dir, model, seconds=60.3, max_seconds=60.0).transcribe(
        b"audio", "audio/webm"
    )
    assert result.audio_seconds == pytest.approx(60.3)


def test_undecodable_audio(bin_dir: Path, work_dir: Path, model: Path) -> None:
    transcriber = make(bin_dir, work_dir, model, ffmpeg_body="sys.exit(1)")

    with pytest.raises(SttBadAudio):
        transcriber.transcribe(b"audio", "audio/webm")
    assert list(work_dir.iterdir()) == []


def test_silent_output_is_bad_audio(bin_dir: Path, work_dir: Path, model: Path) -> None:
    with pytest.raises(SttBadAudio):
        make(bin_dir, work_dir, model, seconds=0).transcribe(b"audio", "audio/webm")


def test_whisper_timeout_kills_and_cleans_up(bin_dir: Path, work_dir: Path, model: Path) -> None:
    transcriber = make(bin_dir, work_dir, model, whisper_body="time.sleep(10)", timeout_seconds=0.5)

    with pytest.raises(SttTimeout):
        transcriber.transcribe(b"audio", "audio/webm")
    assert list(work_dir.iterdir()) == []


def test_ffmpeg_timeout(bin_dir: Path, work_dir: Path, model: Path) -> None:
    transcriber = make(
        bin_dir, work_dir, model, ffmpeg_body="import time; time.sleep(10)", timeout_seconds=0.5
    )

    with pytest.raises(SttTimeout):
        transcriber.transcribe(b"audio", "audio/webm")
    assert list(work_dir.iterdir()) == []


def test_whisper_failure(bin_dir: Path, work_dir: Path, model: Path) -> None:
    transcriber = make(bin_dir, work_dir, model, whisper_body="sys.exit(3)")

    with pytest.raises(SttFailed):
        transcriber.transcribe(b"audio", "audio/webm")
    assert list(work_dir.iterdir()) == []


@pytest.mark.parametrize(
    "payload",
    ["not json", [], {"transcription": []}, {"result": {"language": "en"}}],
)
def test_malformed_whisper_output(
    bin_dir: Path, work_dir: Path, model: Path, payload: object
) -> None:
    with pytest.raises(SttFailed):
        make(bin_dir, work_dir, model, payload=payload).transcribe(b"audio", "audio/webm")
    assert list(work_dir.iterdir()) == []


def test_empty_transcription_has_no_confidence(bin_dir: Path, work_dir: Path, model: Path) -> None:
    payload = {"result": {"language": "en"}, "transcription": []}
    result = make(bin_dir, work_dir, model, payload=payload).transcribe(b"audio", "audio/webm")

    assert result.text == ""
    assert result.confidence is None
    assert result.language_probability is None


def test_missing_model_is_unavailable(bin_dir: Path, work_dir: Path, model: Path) -> None:
    transcriber = make(bin_dir, work_dir, model)
    model.unlink()

    assert transcriber.health().available is False
    assert transcriber.health().model is False
    with pytest.raises(SttUnavailable):
        transcriber.transcribe(b"audio", "audio/webm")


def test_missing_binary_is_unavailable(work_dir: Path, model: Path) -> None:
    transcriber = Transcriber(
        whisper_binary="/nonexistent/whisper-cli", model_path=model, work_dir=work_dir
    )

    health = transcriber.health()
    assert health.available is False
    assert health.whisper is False
    with pytest.raises(SttUnavailable):
        transcriber.transcribe(b"audio", "audio/webm")


def test_health_available(bin_dir: Path, work_dir: Path, model: Path) -> None:
    health = make(bin_dir, work_dir, model).health()

    assert health.available and health.whisper and health.ffmpeg and health.model


def test_runs_one_job_at_a_time(bin_dir: Path, work_dir: Path, model: Path) -> None:
    body = """
with open(os.path.join(here, "spans.log"), "a") as fh:
    fh.write(f"start {time.monotonic()}\\n")
time.sleep(0.3)
with open(os.path.join(here, "spans.log"), "a") as fh:
    fh.write(f"end {time.monotonic()}\\n")
"""
    transcriber = make(bin_dir, work_dir, model, whisper_body=body)
    threads = [
        threading.Thread(target=transcriber.transcribe, args=(b"audio", "audio/webm"))
        for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    events = [line.split()[0] for line in (bin_dir / "spans.log").read_text().splitlines()]
    assert events == ["start", "end"] * 3


def test_async_wrapper(bin_dir: Path, work_dir: Path, model: Path) -> None:
    transcriber = make(bin_dir, work_dir, model)

    result = asyncio.run(transcriber.transcribe_async(b"audio", "audio/wav"))

    assert result.language == "es"


def test_from_settings(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        whisper_binary="wc",
        whisper_model_path=tmp_path / "m.bin",
        whisper_language="auto",
        whisper_threads=2,
        whisper_timeout_seconds=9,
        stt_max_bytes=100,
        stt_max_seconds=10,
    )

    transcriber = Transcriber.from_settings(settings)

    assert transcriber.whisper_binary == "wc"
    assert transcriber.model_path == tmp_path / "m.bin"
    assert transcriber.language == "auto"
    assert (transcriber.threads, transcriber.timeout_seconds) == (2, 9)
    assert (transcriber.max_bytes, transcriber.max_seconds) == (100, 10)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WHISPER_MODEL_PATH", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.whisper_model_path == Path("/var/lib/jyj/whisper/ggml-small.bin")
    assert settings.whisper_language == "es"
    assert settings.whisper_timeout_seconds == 60
    assert settings.whisper_threads == 4
    assert settings.stt_max_bytes == 5 * 1024 * 1024
    assert settings.stt_max_seconds == 60


@pytest.mark.parametrize("language", ["spanish", "ES", "", "es;rm"])
def test_settings_reject_bad_language(language: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, whisper_language=language)  # type: ignore[call-arg]


def test_download_pins_new_models() -> None:
    assert dl.MODELS["small"].filename == "ggml-small.bin"
    assert dl.MODELS["turbo"].filename == "ggml-large-v3-turbo-q5_0.bin"
    assert all(len(model.sha256) == 64 for model in dl.MODELS.values())


def test_download_verifies_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "src.bin"
    source.write_bytes(b"weights")
    good = dl.Model("ggml-tiny.bin", dl.sha256_file(source), 7)
    monkeypatch.setitem(dl.MODELS, "tiny", good)
    target_dir = tmp_path / "models"

    path = dl.download("tiny", target_dir, url=source.as_uri())

    assert path.read_bytes() == b"weights"
    assert sorted(p.name for p in target_dir.iterdir()) == ["ggml-tiny.bin"]


def test_download_rejects_checksum_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src.bin"
    source.write_bytes(b"tampered")
    monkeypatch.setitem(dl.MODELS, "tiny", dl.Model("ggml-tiny.bin", "0" * 64, 8))
    target_dir = tmp_path / "models"

    with pytest.raises(dl.ChecksumMismatch):
        dl.download("tiny", target_dir, url=source.as_uri())
    assert list(target_dir.iterdir()) == []


def test_download_rejects_plain_http(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        dl.download("tiny", tmp_path, url="http://example.com/x.bin")
