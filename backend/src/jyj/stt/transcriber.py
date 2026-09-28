import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jyj.config import Settings, get_settings
from jyj.stt.errors import (
    SttBadAudio,
    SttFailed,
    SttTimeout,
    SttTooLarge,
    SttTooLong,
    SttUnavailable,
)

logger = logging.getLogger(__name__)

# Forcing the demuxer from the declared type stops ffmpeg from probing into formats that can
# reference other files or URLs (hls, concat, ...).
DEMUXERS: dict[str, str] = {
    "audio/webm": "matroska",
    "video/webm": "matroska",
    "audio/mp4": "mov",
    "audio/x-m4a": "mov",
    "audio/aac": "aac",
    "audio/m4a": "mov",
    "video/mp4": "mov",
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
}

MAX_PROMPT_CHARS = 400
# MediaRecorder stops a little after its timer, so allow a small overshoot past the limit.
DURATION_TOLERANCE_SECONDS = 0.5

_LANGUAGE_PATTERN = re.compile(
    r"auto-detected language:\s*([a-z][a-z0-9_-]*)\s*\(p\s*=\s*([0-9.]+)\)", re.IGNORECASE
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: str
    language_probability: float | None
    confidence: float | None
    audio_seconds: float
    elapsed_seconds: float
    model: str


@dataclass(frozen=True, slots=True)
class SttHealth:
    available: bool
    whisper: bool
    ffmpeg: bool
    model: bool


class Transcriber:
    """Runs one transcription at a time: ffmpeg to 16 kHz mono WAV, then whisper-cli."""

    def __init__(
        self,
        *,
        whisper_binary: str,
        model_path: Path,
        language: str = "es",
        threads: int = 4,
        timeout_seconds: float = 30.0,
        max_bytes: int = 5 * 1024 * 1024,
        max_seconds: float = 60.0,
        ffmpeg_binary: str = "ffmpeg",
        work_dir: Path | None = None,
    ) -> None:
        self.whisper_binary = whisper_binary
        self.model_path = model_path
        self.language = language
        self.threads = threads
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds
        self.ffmpeg_binary = ffmpeg_binary
        self.work_dir = work_dir
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "Transcriber":
        return cls(
            whisper_binary=settings.whisper_binary,
            model_path=settings.whisper_model_path,
            language=settings.whisper_language,
            threads=settings.whisper_threads,
            timeout_seconds=settings.whisper_timeout_seconds,
            max_bytes=settings.stt_max_bytes,
            max_seconds=settings.stt_max_seconds,
        )

    def health(self) -> SttHealth:
        whisper = shutil.which(self.whisper_binary) is not None
        ffmpeg = shutil.which(self.ffmpeg_binary) is not None
        model = self.model_path.is_file()
        return SttHealth(
            available=whisper and ffmpeg and model, whisper=whisper, ffmpeg=ffmpeg, model=model
        )

    async def transcribe_async(
        self, audio: bytes, mime_type: str, *, prompt: str = ""
    ) -> TranscriptionResult:
        return await asyncio.to_thread(self.transcribe, audio, mime_type, prompt=prompt)

    def transcribe(self, audio: bytes, mime_type: str, *, prompt: str = "") -> TranscriptionResult:
        if len(audio) > self.max_bytes:
            raise SttTooLarge(f"audio is {len(audio)} bytes, limit is {self.max_bytes}")
        if not audio:
            raise SttBadAudio("audio is empty")
        demuxer = DEMUXERS.get(_base_mime(mime_type))
        if demuxer is None:
            raise SttBadAudio("unsupported audio type")
        whisper = shutil.which(self.whisper_binary)
        ffmpeg = shutil.which(self.ffmpeg_binary)
        if whisper is None or ffmpeg is None or not self.model_path.is_file():
            raise SttUnavailable("whisper-cli, ffmpeg or the model file is missing")

        with self._lock:
            started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix="jyj-stt-", dir=self.work_dir) as tmp:
                root = Path(tmp)
                source = root / "input"
                wav = root / "audio.wav"
                source.write_bytes(audio)
                self._convert(ffmpeg, demuxer, source, wav)
                source.unlink()
                audio_seconds = _wav_seconds(wav)
                if audio_seconds > self.max_seconds + DURATION_TOLERANCE_SECONDS:
                    raise SttTooLong(f"audio is longer than {self.max_seconds:g} s")
                payload, stderr = self._whisper(whisper, wav, root / "result", _clean(prompt))
            result = _parse(
                payload,
                stderr,
                audio_seconds=audio_seconds,
                elapsed_seconds=time.monotonic() - started,
                model=self.model_path.name,
            )
        logger.info(
            "transcribed %.1f s of audio in %.2f s (language=%s confidence=%s)",
            result.audio_seconds,
            result.elapsed_seconds,
            result.language,
            "n/a" if result.confidence is None else f"{result.confidence:.2f}",
        )
        logger.debug("transcript: %s", result.text)
        return result

    def _convert(self, ffmpeg: str, demuxer: str, source: Path, wav: Path) -> None:
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "file",
            "-f",
            demuxer,
            "-i",
            str(source),
            "-t",
            f"{self.max_seconds + DURATION_TOLERANCE_SECONDS + 1:g}",
            "-vn",
            "-sn",
            "-dn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(wav),
        ]
        completed = self._run(command, cwd=source.parent, what="ffmpeg")
        if completed.returncode != 0 or not wav.is_file():
            logger.warning("ffmpeg could not decode audio: %s", _last_line(completed.stderr))
            raise SttBadAudio("audio could not be decoded")

    def _whisper(
        self, whisper: str, wav: Path, output_prefix: Path, prompt: str
    ) -> tuple[object, str]:
        command = [
            whisper,
            "--model",
            str(self.model_path),
            "--file",
            str(wav),
            "--language",
            self.language,
            "--threads",
            str(self.threads),
            "--output-json-full",
            "--output-file",
            str(output_prefix),
            "--no-gpu",
        ]
        if prompt:
            command.extend(("--prompt", prompt))
        completed = self._run(command, cwd=wav.parent, what="whisper-cli")
        if completed.returncode != 0:
            logger.warning(
                "whisper-cli exited %d: %s", completed.returncode, _last_line(completed.stderr)
            )
            raise SttFailed("whisper-cli failed")
        try:
            payload = json.loads(output_prefix.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SttFailed("whisper-cli returned invalid JSON") from error
        return payload, completed.stderr

    def _run(self, command: list[str], *, cwd: Path, what: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603 - fixed argv, no shell
                command,
                cwd=cwd,
                env=_child_env(cwd),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as error:
            raise SttTimeout(f"{what} timed out after {self.timeout_seconds:g} s") from error
        except OSError as error:
            raise SttUnavailable(f"{what} could not be started") from error


@lru_cache
def get_transcriber() -> Transcriber:
    return Transcriber.from_settings(get_settings())


def _child_env(home: Path) -> dict[str, str]:
    # Children get no app secrets from the environment and no real HOME to read.
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "LC_ALL": "C.UTF-8",
    }


def _base_mime(mime_type: str) -> str:
    return mime_type.split(";", 1)[0].strip().lower()


def _clean(prompt: str) -> str:
    return _CONTROL_CHARS.sub(" ", prompt).strip()[:MAX_PROMPT_CHARS]


def _last_line(stderr: str) -> str:
    lines = stderr.strip().splitlines()
    return lines[-1][:200] if lines else ""


def _wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as reader:
            frames, rate = reader.getnframes(), reader.getframerate()
    except (OSError, EOFError, wave.Error) as error:
        raise SttBadAudio("audio could not be decoded") from error
    if frames == 0 or rate <= 0:
        raise SttBadAudio("audio contains no samples")
    return frames / rate


def _parse(
    payload: object, stderr: str, *, audio_seconds: float, elapsed_seconds: float, model: str
) -> TranscriptionResult:
    if not isinstance(payload, dict):
        raise SttFailed("whisper-cli JSON root is not an object")
    result = payload.get("result")
    language = result.get("language") if isinstance(result, dict) else None
    if not isinstance(language, str) or not language:
        raise SttFailed("whisper-cli omitted the detected language")
    segments = payload.get("transcription")
    if not isinstance(segments, list):
        raise SttFailed("whisper-cli omitted transcription segments")

    texts: list[str] = []
    confidences: list[float] = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            continue
        texts.append(segment["text"].strip())
        tokens = segment.get("tokens")
        if not isinstance(tokens, list):
            continue
        for token in tokens:
            if not isinstance(token, dict) or not isinstance(token.get("text"), str):
                continue
            if _special_token(token["text"]):
                continue
            probability = _probability(token.get("p"))
            if probability is not None:
                confidences.append(probability)

    language_probability = None
    match = _LANGUAGE_PATTERN.search(stderr)
    if match and match.group(1).casefold() == language.casefold():
        language_probability = _probability(match.group(2))

    return TranscriptionResult(
        text=" ".join(t for t in texts if t),
        language=language,
        language_probability=language_probability,
        confidence=sum(confidences) / len(confidences) if confidences else None,
        audio_seconds=audio_seconds,
        elapsed_seconds=elapsed_seconds,
        model=model,
    )


def _special_token(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("[_") or (stripped.startswith("<|") and stripped.endswith("|>"))


def _probability(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return min(1.0, max(0.0, number))
