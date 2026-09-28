"""Chat tool registry and the tools the assistant may call."""

from jyj.chat.tools import calendar, ingredients, photos, recipes, shopping, stock
from jyj.chat.tools.registry import (
    DEFAULT_TOOL_TIMEOUT,
    Registry,
    Tool,
    ToolContext,
    ToolRejected,
    ToolResult,
    ToolStatus,
)


def build_registry(tool_timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT) -> Registry:
    registry = Registry(tool_timeout_seconds=tool_timeout_seconds)
    registry.register_all(
        (
            *recipes.TOOLS,
            *photos.TOOLS,
            *ingredients.TOOLS,
            *stock.TOOLS,
            *calendar.TOOLS,
            *shopping.TOOLS,
        )
    )
    return registry


__all__ = [
    "Registry",
    "Tool",
    "ToolContext",
    "ToolRejected",
    "ToolResult",
    "ToolStatus",
    "build_registry",
]
