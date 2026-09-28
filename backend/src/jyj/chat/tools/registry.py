"""Allowlisted, validated, audited execution boundary for chat tools.

The model only ever names a tool and passes JSON arguments. Nothing runs unless the name is
registered and the arguments validate against the tool's Pydantic model; handlers call the
service layer as the acting user with source=chat. Every call, including rejected ones,
leaves a chat_actions row. Tools flagged ``requires_confirmation`` only record a proposal;
``confirm`` runs it later, once.

Results are plain JSON for the model. Service errors become ``rejected`` with their
user-facing message; a call that runs past the Postgres statement_timeout becomes
``timeout``; anything else becomes a generic ``error`` so stack traces, SQL and driver
messages never reach the prompt.
"""

import json
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from jyj.chat.tools.schema import check_strict, strict_schema
from jyj.models import ChatAction, ChatActionStatus, ChatConversation, StockSource, User
from jyj.services.errors import ConflictError, NotFoundError, ServiceError

logger = logging.getLogger(__name__)

TOOL_NAME_MAX = 64
MAX_ERROR_DETAILS = 10
DEFAULT_TOOL_TIMEOUT = 3.0
QUERY_CANCELED = "57014"


class ToolStatus(StrEnum):
    SUCCESS = "success"
    REJECTED = "rejected"
    ERROR = "error"
    NEEDS_CONFIRMATION = "needs_confirmation"
    TIMEOUT = "timeout"


_AUDIT_STATUS = {
    ToolStatus.SUCCESS: ChatActionStatus.EXECUTED,
    ToolStatus.REJECTED: ChatActionStatus.REJECTED,
    ToolStatus.ERROR: ChatActionStatus.FAILED,
    ToolStatus.NEEDS_CONFIRMATION: ChatActionStatus.PROPOSED,
    ToolStatus.TIMEOUT: ChatActionStatus.TIMEOUT,
}


@dataclass(frozen=True, slots=True)
class ToolContext:
    db: Session
    user: User
    conversation_id: int | None = None
    source: StockSource = StockSource.CHAT


Handler = Callable[[ToolContext, Any], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    kind: Literal["read", "write"]
    handler: Handler
    requires_confirmation: bool = False
    # Read-only check run before a proposal is stored: rejects missing ids early and returns
    # what the confirmation card should show.
    preview: Handler | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool: str
    status: ToolStatus
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    action_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.SUCCESS

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"tool": self.tool, "status": self.status.value}
        if self.action_id is not None:
            out["action_id"] = self.action_id
        if self.data is not None:
            out["data"] = self.data
        if self.error is not None:
            out["error"] = self.error
        return out


class ToolRejected(Exception):
    """Raised by handlers for a refusal whose message is safe to show the model."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Registry:
    # None leaves the session's statement_timeout alone.
    tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT
    _tools: dict[str, Tool] = field(default_factory=dict)
    _schemas: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if not tool.name or len(tool.name) > TOOL_NAME_MAX or not tool.name.isidentifier():
            raise ValueError(f"invalid tool name {tool.name!r}")
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        if tool.args_model.model_config.get("extra") != "forbid":
            raise ValueError(f"{tool.name}: args model must use extra='forbid'")
        if tool.requires_confirmation and tool.kind != "write":
            raise ValueError(f"{tool.name}: only write tools can require confirmation")
        schema = strict_schema(tool.args_model)
        check_strict(schema, path=tool.name)
        self._tools[tool.name] = tool
        self._schemas[tool.name] = schema

    def register_all(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def args_schema(self, name: str) -> dict[str, Any]:
        return self._schemas[name]

    def output_schema(self) -> dict[str, Any]:
        """Schema for one assistant turn: a reply, write ``actions`` and read ``needs``."""
        return {
            "type": "object",
            "properties": {
                "reply": {"type": "string", "description": "Message shown to the user."},
                "actions": {
                    "type": "array",
                    "description": "Write tools to run (or propose, when they need confirmation).",
                    "items": self._calls_schema("write"),
                },
                "needs": {
                    "type": "array",
                    "description": "Read tools whose results you need before answering.",
                    "items": self._calls_schema("read"),
                },
            },
            "required": ["reply", "actions", "needs"],
            "additionalProperties": False,
        }

    def _calls_schema(self, kind: str) -> dict[str, Any]:
        calls = [
            {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": [tool.name]},
                    "args": self._schemas[tool.name],
                },
                "required": ["tool", "args"],
                "additionalProperties": False,
            }
            for tool in self._tools.values()
            if tool.kind == kind
        ]
        if not calls:
            raise ValueError(f"output_schema needs at least one {kind} tool")
        return {"anyOf": calls}

    def catalog_text(self) -> str:
        lines = []
        for kind, heading in (("read", "Read tools (use in needs)"), ("write", "Write tools")):
            tools = [t for t in self._tools.values() if t.kind == kind]
            if not tools:
                continue
            lines.append(f"{heading}:")
            for tool in tools:
                flag = " [asks the user to confirm first]" if tool.requires_confirmation else ""
                args = _signature(self._schemas[tool.name])
                lines.append(f"- {tool.name}({args}){flag}: {tool.description}")
        return "\n".join(lines)

    def execute(
        self,
        name: str,
        raw_args: Any,
        ctx: ToolContext,
        *,
        propose: bool = False,
        batch_id: uuid.UUID | None = None,
    ) -> ToolResult:
        """Run a call, or only record it as a proposal when the tool requires confirmation
        or ``propose`` is set (write tools only). ``batch_id`` groups proposals that are
        confirmed together."""
        tool = self._tools.get(name) if isinstance(name, str) else None
        stored_args = _jsonable(raw_args)
        if tool is None:
            result = _rejected(str(name), "unknown_tool", f"unknown tool {str(name)[:64]!r}")
            return self._audit(ctx, str(name), stored_args, result, batch_id=batch_id)

        args, error = _validate(tool, raw_args)
        if error is not None:
            return self._audit(ctx, name, stored_args, error, batch_id=batch_id)

        if tool.requires_confirmation or (propose and tool.kind == "write"):
            result = self._run(tool, ctx, args, tool.preview, ToolStatus.NEEDS_CONFIRMATION)
            return self._audit(ctx, name, stored_args, result, batch_id=batch_id)

        result = self._run(tool, ctx, args, tool.handler, ToolStatus.SUCCESS)
        return self._audit(ctx, name, stored_args, result, executed=True, batch_id=batch_id)

    def confirm(self, ctx: ToolContext, action_id: int) -> ToolResult:
        """Run a proposed action as ``ctx.user``, who must have requested it or own its
        conversation; a second confirm is rejected."""
        action = self._lock_proposed(ctx, action_id)
        if isinstance(action, ToolResult):
            return action
        tool = self._tools.get(action.tool)
        if tool is None:
            result = _rejected(action.tool, "unknown_tool", "tool is no longer available")
        else:
            args, result = _validate(tool, action.arguments)
            if result is None:
                result = self._run(tool, ctx, args, tool.handler, ToolStatus.SUCCESS)
        result = ToolResult(action.tool, result.status, result.data, result.error, action.id)
        action.status = _AUDIT_STATUS[result.status]
        action.result = _result_payload(result)
        action.confirmed_by = ctx.user.id
        action.executed_at = datetime.now(UTC) if result.ok else None
        ctx.db.flush()
        return result

    def reject(self, ctx: ToolContext, action_id: int) -> ToolResult:
        """Decline a proposed action; it can no longer be confirmed."""
        action = self._lock_proposed(ctx, action_id)
        if isinstance(action, ToolResult):
            return action
        action.status = ChatActionStatus.REJECTED
        action.result = {"error": {"code": "declined", "message": "the user declined"}}
        action.confirmed_by = ctx.user.id
        ctx.db.flush()
        return ToolResult(
            action.tool, ToolStatus.REJECTED, error=action.result["error"], action_id=action.id
        )

    def _lock_proposed(self, ctx: ToolContext, action_id: int) -> ChatAction | ToolResult:
        action = ctx.db.scalars(
            select(ChatAction)
            .where(ChatAction.id == action_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        # Someone else's proposal looks exactly like a missing one.
        if action is None or not _owns(ctx, action):
            return _rejected("", "not_found", f"action {action_id} not found")
        if action.status is not ChatActionStatus.PROPOSED:
            return ToolResult(
                action.tool,
                ToolStatus.REJECTED,
                error={"code": "not_pending", "message": f"action is already {action.status}"},
                action_id=action.id,
            )
        return action

    def _run(
        self,
        tool: Tool,
        ctx: ToolContext,
        args: BaseModel,
        handler: Handler | None,
        ok_status: ToolStatus,
    ) -> ToolResult:
        if handler is None:
            return ToolResult(tool.name, ok_status, data={})
        # A savepoint per call: a failing handler leaves no partial writes behind while the
        # audit row, written afterwards, still lands in the caller's transaction. Reads and
        # previews are always rolled back so they cannot change anything even by mistake.
        read_only = tool.kind == "read" or ok_status is ToolStatus.NEEDS_CONFIRMATION
        savepoint = ctx.db.begin_nested()
        try:
            restore = self._limit_statements(ctx.db)
            payload = json.loads(json.dumps(handler(ctx, args), allow_nan=False))
            if not isinstance(payload, dict):
                raise TypeError("tool handlers must return an object")
            restore()
        except DBAPIError as exc:
            savepoint.rollback()
            if getattr(exc.orig, "sqlstate", None) != QUERY_CANCELED:
                logger.exception("chat tool %s failed", tool.name)
                return _internal_error(tool.name)
            logger.warning("chat tool %s hit its statement timeout", tool.name)
            return ToolResult(
                tool.name,
                ToolStatus.TIMEOUT,
                error={
                    "code": "timeout",
                    "message": "the tool took too long; try a narrower request",
                },
            )
        except ToolRejected as exc:
            savepoint.rollback()
            return _rejected(tool.name, exc.code, exc.message)
        except ServiceError as exc:
            savepoint.rollback()
            return _rejected(tool.name, _service_code(exc), exc.detail, exc.extensions)
        except Exception:
            savepoint.rollback()
            logger.exception("chat tool %s failed", tool.name)
            return _internal_error(tool.name)
        if read_only:
            savepoint.rollback()
        else:
            savepoint.commit()
        return ToolResult(tool.name, ok_status, data=payload)

    def _limit_statements(self, db: Session) -> Callable[[], None]:
        """SET LOCAL statement_timeout for this call; the returned callable puts it back.

        A rolled-back savepoint undoes the SET by itself; a released one would keep it for
        the rest of the transaction, so successful calls restore the previous value.
        """
        if self.tool_timeout_seconds is None:
            return lambda: None
        previous = db.scalar(text("SELECT current_setting('statement_timeout')"))
        limit = f"{max(1, round(self.tool_timeout_seconds * 1000))}ms"
        db.execute(text("SELECT set_config('statement_timeout', :limit, true)"), {"limit": limit})
        return lambda: db.execute(
            text("SELECT set_config('statement_timeout', :previous, true)"), {"previous": previous}
        )

    def _audit(
        self,
        ctx: ToolContext,
        name: str,
        arguments: Any,
        result: ToolResult,
        *,
        executed: bool = False,
        batch_id: uuid.UUID | None = None,
    ) -> ToolResult:
        action = ChatAction(
            conversation_id=ctx.conversation_id,
            batch_id=batch_id,
            tool=name[:TOOL_NAME_MAX],
            arguments=arguments,
            status=_AUDIT_STATUS[result.status],
            result=_result_payload(result),
            requested_by=ctx.user.id,
            executed_at=datetime.now(UTC) if executed and result.ok else None,
        )
        ctx.db.add(action)
        ctx.db.flush()
        return ToolResult(result.tool, result.status, result.data, result.error, action.id)


def _owns(ctx: ToolContext, action: ChatAction) -> bool:
    if action.requested_by == ctx.user.id:
        return True
    if action.conversation_id is None:
        return False
    owner = ctx.db.scalar(
        select(ChatConversation.user_id).where(ChatConversation.id == action.conversation_id)
    )
    return owner == ctx.user.id


def _validate(tool: Tool, raw_args: Any) -> tuple[BaseModel | None, ToolResult | None]:
    if not isinstance(raw_args, Mapping):
        return None, _rejected(tool.name, "invalid_arguments", "args must be an object")
    try:
        return tool.args_model.model_validate(dict(raw_args)), None
    except ValidationError as exc:
        details = [
            {"field": ".".join(str(p) for p in err["loc"]) or "args", "problem": err["msg"]}
            for err in exc.errors(include_url=False, include_input=False, include_context=False)
        ][:MAX_ERROR_DETAILS]
        return None, ToolResult(
            tool.name,
            ToolStatus.REJECTED,
            error={
                "code": "invalid_arguments",
                "message": "arguments do not match the tool schema",
                "details": details,
            },
        )


def _internal_error(tool: str) -> ToolResult:
    return ToolResult(
        tool,
        ToolStatus.ERROR,
        error={"code": "internal_error", "message": "the tool failed unexpectedly"},
    )


def _rejected(
    tool: str, code: str, message: str, extensions: Mapping[str, Any] | None = None
) -> ToolResult:
    error: dict[str, Any] = {"code": code, "message": message}
    if extensions:
        error["details"] = _jsonable(dict(extensions))
    return ToolResult(tool, ToolStatus.REJECTED, error=error)


def _service_code(exc: ServiceError) -> str:
    if isinstance(exc, NotFoundError):
        return "not_found"
    if isinstance(exc, ConflictError):
        return "conflict"
    return "invalid"


def _result_payload(result: ToolResult) -> dict[str, Any]:
    if result.error is not None:
        return {"error": result.error}
    return {"data": result.data}


def _jsonable(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str, allow_nan=False))
    except (TypeError, ValueError):
        return {"unserialisable": type(value).__name__}


def _signature(schema: dict[str, Any]) -> str:
    return ", ".join(f"{key}: {_type_name(prop)}" for key, prop in schema["properties"].items())


def _type_name(schema: dict[str, Any]) -> str:
    if "anyOf" in schema:
        return "|".join(_type_name(opt) for opt in schema["anyOf"])
    if "enum" in schema:
        return "|".join(json.dumps(v) for v in schema["enum"])
    if schema["type"] == "array":
        return f"list[{_type_name(schema['items'])}]"
    return schema["type"]
