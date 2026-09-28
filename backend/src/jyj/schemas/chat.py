from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jyj.chat.orchestrator import TEXT_MAX
from jyj.models import ChatInput, ChatRole


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=TEXT_MAX)
    input: ChatInput = ChatInput.TEXT
    transcript_confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _confidence_only_for_voice(self) -> Self:
        if self.transcript_confidence is not None and self.input is not ChatInput.VOICE:
            raise ValueError("transcript_confidence is only for voice input")
        if not self.text.strip():
            raise ValueError("text must not be blank")
        return self


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: ChatRole
    content: str
    input: ChatInput | None
    transcript_confidence: float | None
    created_at: datetime


class ActionOut(BaseModel):
    id: int
    message_id: int | None
    tool: str
    status: str
    summary: str
    arguments: Any
    data: Any | None = None
    error: Any | None = None
    created_at: datetime
    executed_at: datetime | None


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
    actions: list[ActionOut]
