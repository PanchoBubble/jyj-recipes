"""Voice input: transcribe a recording so the user can review it before sending it as chat."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from jyj.api.auth import CurrentUser, current_user
from jyj.config import get_settings
from jyj.db import get_db
from jyj.models import Ingredient, Recipe
from jyj.services.rate_limit import SlidingWindowLimiter
from jyj.stt import (
    SttBadAudio,
    SttError,
    SttFailed,
    SttTimeout,
    SttTooLarge,
    SttTooLong,
    SttUnavailable,
    Transcriber,
    get_transcriber,
)
from jyj.stt.transcriber import MAX_PROMPT_CHARS

logger = logging.getLogger(__name__)

TRANSCRIBE_PATH = "/chat/transcribe"
# Room for multipart framing around a maximum-size recording.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
ACCEPTED_TYPES = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
        "audio/aac",
        "audio/x-m4a",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
    }
)
PROMPT_NAMES_PER_KIND = 60
READ_CHUNK = 64 * 1024
UNAVAILABLE_DETAIL = (
    "Speech to text is not installed. An admin needs to run `make whisper-download` "
    "on the server (whisper-cli and ffmpeg ship in the backend image)."
)

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(current_user)])

Db = Annotated[Session, Depends(get_db)]
Stt = Annotated[Transcriber, Depends(get_transcriber)]


class TranscriptOut(BaseModel):
    text: str
    language: str
    confidence: float | None
    low_confidence: bool
    duration_seconds: float


def transcribe_limit_bytes() -> int:
    return get_settings().stt_max_bytes + MULTIPART_OVERHEAD_BYTES


def new_transcribe_limiter() -> SlidingWindowLimiter:
    return SlidingWindowLimiter(limit=get_settings().stt_rate_limit_per_minute, window=60.0)


def get_transcribe_limiter(request: Request) -> SlidingWindowLimiter:
    return request.app.state.transcribe_limiter


def stt_health(transcriber: Transcriber) -> dict[str, Any]:
    health = transcriber.health()
    if health.available:
        detail = "ready"
    elif not health.whisper:
        detail = "whisper_missing"
    elif not health.ffmpeg:
        detail = "ffmpeg_missing"
    else:
        detail = "model_missing"
    return {"available": health.available, "detail": detail, "model": transcriber.model_path.name}


@router.post("/transcribe", response_model=TranscriptOut)
def transcribe(
    audio: Annotated[UploadFile, File()],
    user: CurrentUser,
    db: Db,
    transcriber: Stt,
    limiter: Annotated[SlidingWindowLimiter, Depends(get_transcribe_limiter)],
) -> TranscriptOut:
    # Sync on purpose: FastAPI runs it in the threadpool while ffmpeg and whisper-cli block.
    retry_after = limiter.acquire(str(user.id))
    if retry_after > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many transcriptions, try again shortly",
            headers={"Retry-After": str(max(1, round(retry_after)))},
        )
    mime_type = audio.content_type or ""
    if mime_type.split(";", 1)[0].strip().lower() not in ACCEPTED_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Audio must be webm, ogg, mp4/aac or wav"
        )
    data = _read_capped(audio, transcriber.max_bytes)
    prompt = domain_prompt(db)
    # The prompt is built; release the connection before the long-running transcription.
    db.commit()

    try:
        result = transcriber.transcribe(data, mime_type, prompt=prompt)
    except SttError as error:
        raise _http_error(error) from error

    threshold = get_settings().stt_low_confidence
    low = result.confidence is None or result.confidence < threshold or not result.text.strip()
    return TranscriptOut(
        text=result.text,
        language=result.language,
        confidence=result.confidence,
        low_confidence=low,
        duration_seconds=round(result.audio_seconds, 2),
    )


def domain_prompt(db: Session) -> str:
    """Recipe and ingredient names, most recent first, to bias whisper towards them."""
    recipes = db.scalars(
        select(Recipe.name)
        .where(Recipe.archived_at.is_(None))
        .order_by(Recipe.id.desc())
        .limit(PROMPT_NAMES_PER_KIND)
    ).all()
    ingredients = db.scalars(
        select(Ingredient.name).order_by(Ingredient.id.desc()).limit(PROMPT_NAMES_PER_KIND)
    ).all()
    prompt = ""
    for name in dict.fromkeys(n.strip() for n in [*recipes, *ingredients] if n.strip()):
        candidate = f"{prompt}, {name}" if prompt else name
        if len(candidate) > MAX_PROMPT_CHARS:
            break
        prompt = candidate
    return prompt


def _read_capped(upload: UploadFile, limit: int) -> bytes:
    buf = bytearray()
    while chunk := upload.file.read(READ_CHUNK):
        buf += chunk
        if len(buf) > limit:
            raise _http_error(SttTooLarge())
    return bytes(buf)


def _http_error(error: SttError) -> HTTPException:
    settings = get_settings()
    if isinstance(error, SttTooLarge):
        mib = settings.stt_max_bytes / (1024 * 1024)
        return HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"Recording must be at most {mib:g} MiB"
        )
    if isinstance(error, SttTooLong):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Recording must be at most {settings.stt_max_seconds:g} seconds",
        )
    if isinstance(error, SttBadAudio):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Audio could not be decoded")
    if isinstance(error, SttUnavailable):
        logger.warning("transcription unavailable: %s", error)
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE_DETAIL)
    if isinstance(error, SttTimeout):
        return HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "Transcription timed out")
    if isinstance(error, SttFailed):
        logger.warning("transcription failed: %s", error)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "Transcription failed")
