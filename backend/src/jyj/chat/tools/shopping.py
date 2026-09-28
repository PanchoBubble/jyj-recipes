"""Shopping tools: preview a range and snapshot it as a list.

Deliberately read/create only. Ticking items off and completing a list (which writes
'purchased' stock) stay a human action in the app.
"""

import datetime as dt

from jyj.chat.tools.common import DateRange, number
from jyj.chat.tools.registry import Tool, ToolContext
from jyj.models import ShoppingItemKind
from jyj.services import shopping_lists as shopping_service


class ShoppingRangeArgs(DateRange):
    pass


def today() -> dt.date:
    return dt.date.today()


def preview_shopping(ctx: ToolContext, args: ShoppingRangeArgs) -> dict:
    plan = shopping_service.preview(ctx.db, args.start, args.end, today())
    to_buy = [
        {
            "ingredient_id": item.ingredient_id,
            "name": item.name,
            "buy": f"{number(item.display.amount)} {item.display.unit.code}",
            "category": item.category,
        }
        for item in plan.items
        if item.to_buy_base > 0
    ]
    return {
        "from": plan.start.isoformat(),
        "to": plan.end.isoformat(),
        "to_buy": to_buy,
        "unconverted_count": sum(1 for item in plan.items if item.unconverted),
        "to_taste_count": len(plan.check_have),
    }


def create_shopping_list(ctx: ToolContext, args: ShoppingRangeArgs) -> dict:
    shopping_list = shopping_service.create_list(ctx.db, ctx.user, args.start, args.end, today())
    kinds = [item.kind for item in shopping_list.items]
    return {
        "list_id": shopping_list.id,
        "from": shopping_list.start_date.isoformat(),
        "to": shopping_list.end_date.isoformat(),
        "item_count": len(kinds),
        "to_buy_count": kinds.count(ShoppingItemKind.BUY),
    }


TOOLS = (
    Tool(
        name="preview_shopping",
        description=(
            "What to buy for meals planned between two ISO dates (at most 31 days), after "
            "stock and earlier planned meals. Nothing is saved."
        ),
        args_model=ShoppingRangeArgs,
        kind="read",
        handler=preview_shopping,
    ),
    Tool(
        name="create_shopping_list",
        description=(
            "Save the shopping list for a date range so the user can tick it off in the app. "
            "Completing a list is only done by the user in the app."
        ),
        args_model=ShoppingRangeArgs,
        kind="write",
        handler=create_shopping_list,
    ),
)
