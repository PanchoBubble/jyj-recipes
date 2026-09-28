from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jyj.db import Base, TimestampMixin


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


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatInput(StrEnum):
    TEXT = "text"
    VOICE = "voice"


chat_role_enum = Enum(
    ChatRole, name="chat_role", values_callable=lambda members: [m.value for m in members]
)
chat_input_enum = Enum(
    ChatInput, name="chat_input", values_callable=lambda members: [m.value for m in members]
)


class ChatConversation(TimestampMixin, Base):
    """One chat thread, owned by the user who started it."""

    __tablename__ = "chat_conversations"
    __table_args__ = (
        Index("ix_chat_conversations_user_id_updated_at", "user_id", "updated_at"),
        Index("ix_chat_conversations_updated_at", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(120))

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        order_by="ChatMessage.id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ChatMessage(Base):
    """A user request, an assistant reply, or the tool results that followed it."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_conversation_id", "conversation_id", "id"),
        Index("ix_chat_messages_created_at", "created_at"),
        CheckConstraint(
            "transcript_confidence IS NULL OR (transcript_confidence >= 0 AND "
            "transcript_confidence <= 1)",
            name="transcript_confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE")
    )
    role: Mapped[ChatRole] = mapped_column(chat_role_enum)
    content: Mapped[str] = mapped_column(Text)
    # Only set on user messages.
    input: Mapped[ChatInput | None] = mapped_column(chat_input_enum)
    transcript_confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")


class ChatAction(Base):
    """Audit row for every tool call the chat assistant makes, including rejected ones."""

    __tablename__ = "chat_actions"
    __table_args__ = (
        Index("ix_chat_actions_conversation_id", "conversation_id"),
        Index("ix_chat_actions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # SET NULL keeps the audit trail when retention purges the conversation text.
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="SET NULL")
    )
    # The assistant message whose turn produced this call, for rendering action cards.
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), index=True
    )
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[Any] = mapped_column(JSONB)
    status: Mapped[ChatActionStatus] = mapped_column(chat_action_status_enum)
    result: Mapped[Any | None] = mapped_column(JSONB)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
