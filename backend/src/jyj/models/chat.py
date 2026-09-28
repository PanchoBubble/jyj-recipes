from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from jyj.db import Base


class ChatActionStatus(StrEnum):
    PROPOSED = "proposed"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


chat_action_status_enum = Enum(
    ChatActionStatus,
    name="chat_action_status",
    values_callable=lambda members: [m.value for m in members],
)


class ChatAction(Base):
    """Audit row for every tool call the chat assistant makes, including rejected ones."""

    __tablename__ = "chat_actions"
    __table_args__ = (
        Index("ix_chat_actions_conversation_id", "conversation_id"),
        Index("ix_chat_actions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Plain int until chat_conversations exists; the FK gets added with that table.
    conversation_id: Mapped[int | None]
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[ChatActionStatus] = mapped_column(chat_action_status_enum)
    result: Mapped[Any | None] = mapped_column(JSONB)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
