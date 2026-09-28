from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from jyj.api.auth import current_user
from jyj.chat.provider import CodexProvider
from jyj.config import get_settings

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(current_user)])


@lru_cache
def get_codex_provider() -> CodexProvider:
    settings = get_settings()
    return CodexProvider(
        binary=settings.codex_binary,
        model=settings.codex_model,
        timeout=settings.codex_timeout_seconds,
        enabled=settings.codex_enabled,
    )


@router.get("/health")
def chat_health(
    provider: Annotated[CodexProvider, Depends(get_codex_provider)],
) -> dict[str, Any]:
    return {"codex": provider.health().as_dict()}
