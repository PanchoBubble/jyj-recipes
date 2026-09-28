"""Chat tool registry and the tools the assistant may call."""

from jyj.chat.tools import calendar, ingredients, recipes, shopping, stock
from jyj.chat.tools.registry import (
    Registry,
    Tool,
    ToolContext,
    ToolRejected,
    ToolResult,
    ToolStatus,
)


def build_registry() -> Registry:
    registry = Registry()
    registry.register_all(
        (*recipes.TOOLS, *ingredients.TOOLS, *stock.TOOLS, *calendar.TOOLS, *shopping.TOOLS)
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
