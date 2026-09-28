import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Callable
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from jyj.api.auth import CurrentUser, current_user
from jyj.chat import conversations
from jyj.chat.orchestrator import (
    ChatEvent,
    Emit,
    decide,
    error_event,
    run_turn,
    sanitized_error,
    summarize,
)
from jyj.chat.provider import CodexProvider
from jyj.chat.tools import Registry, ToolStatus, build_registry
from jyj.config import get_settings
from jyj.db import get_db, get_sessionmaker
from jyj.models import ChatAction, ChatConversation, User
from jyj.schemas.chat import ActionOut, ConversationDetail, ConversationOut, MessageIn

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 15.0

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(current_user)])

Db = Annotated[Session, Depends(get_db)]


@lru_cache
def get_codex_provider() -> CodexProvider:
    settings = get_settings()
    return CodexProvider(
        binary=settings.codex_binary,
        model=settings.codex_model,
        timeout=settings.codex_timeout_seconds,
        enabled=settings.codex_enabled,
    )


@lru_cache
def get_chat_registry() -> Registry:
    return build_registry()


def get_chat_session_factory() -> Callable[[], Session]:
    """Sessions for the turn's worker thread, which outlives the request's own session."""
    return get_sessionmaker()


Provider = Annotated[CodexProvider, Depends(get_codex_provider)]
ChatRegistry = Annotated[Registry, Depends(get_chat_registry)]
SessionFactory = Annotated[Callable[[], Session], Depends(get_chat_session_factory)]


@router.get("/health")
def chat_health(provider: Provider) -> dict[str, Any]:
    return {"codex": provider.health().as_dict()}


@router.post("/conversations", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(user: CurrentUser, db: Db) -> ChatConversation:
    return conversations.create_conversation(db, user)


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    user: CurrentUser, db: Db, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[ChatConversation]:
    return conversations.list_conversations(db, user, limit)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int, user: CurrentUser, db: Db, registry: ChatRegistry
) -> ConversationDetail:
    conversation = conversations.get_conversation(db, user, conversation_id)
    shown = [
        action_out(a)
        for a in conversations.actions(db, conversation.id)
        if (tool := registry.get(a.tool)) is None or tool.kind == "write"
    ]
    return ConversationDetail.model_validate(
        {
            **ConversationOut.model_validate(conversation).model_dump(),
            "messages": conversations.messages(db, conversation.id),
            "actions": shown,
        }
    )


@router.post("/conversations/{conversation_id}/messages")
def post_message(
    conversation_id: int,
    body: MessageIn,
    user: CurrentUser,
    db: Db,
    provider: Provider,
    registry: ChatRegistry,
    factory: SessionFactory,
) -> StreamingResponse:
    """Run one turn and stream it as Server-Sent Events.

    Events: ``status`` (thinking / running_tool), ``action`` (one per write call),
    ``assistant`` (the reply), ``error`` (sanitized) and a final ``done``.
    """
    conversations.get_conversation(db, user, conversation_id)
    user_id = user.id
    # The request session stays open until the response ends; commit now so the auth
    # refresh does not hold row locks or a connection for the length of the turn.
    db.commit()

    def work(emit: Emit, cancel: threading.Event) -> None:
        with factory() as session:
            acting = session.get(User, user_id)
            if acting is None:
                emit(error_event("not_found", "User not found"))
                return
            conversation = conversations.get_conversation(session, acting, conversation_id)
            result = run_turn(
                session,
                acting,
                conversation,
                body.text,
                body.input,
                emit,
                provider=provider,
                registry=registry,
                cancel=cancel,
                transcript_confidence=body.transcript_confidence,
            )
            emit(
                ChatEvent(
                    "done",
                    {
                        "user_message_id": result.user_message_id,
                        "assistant_message_id": result.assistant_message_id,
                    },
                )
            )

    return StreamingResponse(
        stream_events(work),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/actions/{action_id}/confirm", response_model=ActionOut)
def confirm_action(action_id: int, user: CurrentUser, db: Db, registry: ChatRegistry) -> ActionOut:
    return _decide(db, user, action_id, registry, confirm=True)


@router.post("/actions/{action_id}/reject", response_model=ActionOut)
def reject_action(action_id: int, user: CurrentUser, db: Db, registry: ChatRegistry) -> ActionOut:
    return _decide(db, user, action_id, registry, confirm=False)


def _decide(
    db: Session, user: User, action_id: int, registry: Registry, *, confirm: bool
) -> ActionOut:
    result = decide(db, user, action_id, registry, confirm=confirm)
    if result.status is ToolStatus.REJECTED and (result.error or {}).get("code") == "not_pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Action is no longer pending")
    return action_out(conversations.get_owned_action(db, user, action_id))


def action_out(action: ChatAction) -> ActionOut:
    result = action.result or {}
    data = result.get("data") if isinstance(result, dict) else None
    return ActionOut(
        id=action.id,
        message_id=action.message_id,
        tool=action.tool,
        status=action.status.value,
        summary=summarize(action.tool, action.arguments, data),
        arguments=action.arguments,
        data=data,
        error=result.get("error") if isinstance(result, dict) else None,
        created_at=action.created_at,
        executed_at=action.executed_at,
    )


# --- SSE ------------------------------------------------------------------------------------


def format_sse(event: ChatEvent) -> str:
    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event.type}\ndata: {payload}\n\n"


async def stream_events(
    work: Callable[[Emit, threading.Event], None],
    *,
    keepalive: float = KEEPALIVE_SECONDS,
) -> AsyncIterator[str]:
    """Run ``work`` in a thread and relay what it emits; closing the stream cancels it.

    Starlette closes this generator when the client disconnects, which sets ``cancel`` so
    the provider stops the Codex process and no writes run.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[ChatEvent | None] = asyncio.Queue()
    cancel = threading.Event()

    def emit(event: ChatEvent) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run() -> None:
        try:
            work(emit, cancel)
        except Exception as exc:
            logger.exception("chat stream worker failed")
            emit(error_event(*sanitized_error(exc)))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    worker = loop.run_in_executor(None, run)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            yield format_sse(event)
        await worker
    finally:
        cancel.set()
