"""Conversation storage: ownership-checked lookups, history and the retention purge."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from jyj.models import ChatAction, ChatActionStatus, ChatConversation, ChatMessage, User
from jyj.services.errors import NotFoundError

RETENTION_DAYS = 90
TITLE_MAX = 120
RECENT_LIMIT_MAX = 100


def create_conversation(db: Session, user: User) -> ChatConversation:
    conversation = ChatConversation(user_id=user.id)
    db.add(conversation)
    db.flush()
    db.refresh(conversation)
    return conversation


def get_conversation(
    db: Session, user: User, conversation_id: int, *, lock: bool = False
) -> ChatConversation:
    """The caller's own conversation; someone else's is indistinguishable from a missing one."""
    stmt = select(ChatConversation).where(
        ChatConversation.id == conversation_id, ChatConversation.user_id == user.id
    )
    if lock:
        stmt = stmt.with_for_update()
    conversation = db.scalars(stmt).one_or_none()
    if conversation is None:
        raise NotFoundError("Conversation not found")
    return conversation


def list_conversations(db: Session, user: User, limit: int = 20) -> list[ChatConversation]:
    return list(
        db.scalars(
            select(ChatConversation)
            .where(ChatConversation.user_id == user.id)
            .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc())
            .limit(min(limit, RECENT_LIMIT_MAX))
        )
    )


def messages(db: Session, conversation_id: int) -> list[ChatMessage]:
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.id)
        )
    )


def recent_messages(
    db: Session, conversation_id: int, limit: int, *, before_id: int | None = None
) -> list[ChatMessage]:
    stmt = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
    if before_id is not None:
        stmt = stmt.where(ChatMessage.id < before_id)
    newest = db.scalars(stmt.order_by(ChatMessage.id.desc()).limit(limit)).all()
    return list(reversed(newest))


def actions(db: Session, conversation_id: int) -> list[ChatAction]:
    return list(
        db.scalars(
            select(ChatAction)
            .where(ChatAction.conversation_id == conversation_id)
            .order_by(ChatAction.id)
        )
    )


def get_owned_action(db: Session, user: User, action_id: int) -> ChatAction:
    """An action from one of the caller's conversations, else 404."""
    action = db.scalars(
        select(ChatAction)
        .join(ChatConversation, ChatConversation.id == ChatAction.conversation_id)
        .where(ChatAction.id == action_id, ChatConversation.user_id == user.id)
    ).one_or_none()
    if action is None:
        raise NotFoundError("Action not found")
    return action


def get_owned_batch(
    db: Session, user: User, batch_id: uuid.UUID, *, lock: bool = False
) -> list[ChatAction]:
    """A batch's actions in proposal order, from one of the caller's conversations, else 404.

    ``lock`` takes the rows FOR UPDATE so two decisions on one batch run one after the other.
    """
    stmt = (
        select(ChatAction)
        .join(ChatConversation, ChatConversation.id == ChatAction.conversation_id)
        .where(ChatAction.batch_id == batch_id, ChatConversation.user_id == user.id)
        .order_by(ChatAction.id)
    )
    if lock:
        stmt = stmt.with_for_update(of=ChatAction).execution_options(populate_existing=True)
    batch = list(db.scalars(stmt))
    if not batch:
        raise NotFoundError("Batch not found")
    return batch


def touch(db: Session, conversation: ChatConversation, first_text: str | None = None) -> None:
    conversation.updated_at = func.now()
    if conversation.title is None and first_text and first_text.strip():
        title = " ".join(first_text.split())
        conversation.title = title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1] + "…"


@dataclass(frozen=True, slots=True)
class PurgeResult:
    conversations: int
    messages: int
    expired_proposals: int


def purge(db: Session, *, days: int = RETENTION_DAYS, now: datetime | None = None) -> PurgeResult:
    """Delete chat text older than ``days``.

    Messages past the cutoff go even from conversations that are still active, and
    conversations idle past the cutoff go with everything in them. Audit rows in
    chat_actions stay (their conversation link is nulled); proposals nobody can reach any
    more are closed so they can never be confirmed.
    """
    if days < 1:
        raise ValueError("retention must be at least one day")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    stale = select(ChatConversation.id).where(ChatConversation.updated_at < cutoff)
    expired = db.execute(
        update(ChatAction)
        .where(
            ChatAction.status == ChatActionStatus.PROPOSED,
            ChatAction.conversation_id.in_(stale),
        )
        .values(
            status=ChatActionStatus.REJECTED,
            result={"error": {"code": "expired", "message": "the conversation was purged"}},
        )
    ).rowcount
    old_messages = db.execute(
        delete(ChatMessage).where(
            ChatMessage.created_at < cutoff, ChatMessage.conversation_id.not_in(stale)
        )
    ).rowcount
    stale_messages = db.scalar(
        select(func.count()).select_from(ChatMessage).where(ChatMessage.conversation_id.in_(stale))
    )
    conversations = db.execute(
        delete(ChatConversation).where(ChatConversation.updated_at < cutoff)
    ).rowcount
    db.expire_all()
    return PurgeResult(conversations, old_messages + (stale_messages or 0), expired)
