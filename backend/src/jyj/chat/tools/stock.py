"""Stock tools: thin wrappers over ``jyj.services.stock``."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from jyj.chat.tools.common import Amount, Id, UnitCode, quantity
from jyj.chat.tools.registry import Tool, ToolContext
from jyj.models import StockReason
from jyj.services import ingredients as ingredients_service
from jyj.services import stock as stock_service
from jyj.services.stock import StockChange

IDS_MAX = 50


class GetStockArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient_ids: Annotated[list[Id], Field(max_length=IDS_MAX)] | None = Field(
        default=None, description="Only these ingredients; null lists everything in stock."
    )


class AdjustStockArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient_id: Id
    delta: Amount = Field(description="Signed change: positive adds, negative uses up.")
    unit: UnitCode
    reason: Literal["manual", "purchased", "correction"] | None = None


class SetStockArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient_id: Id
    set_to: Amount = Field(ge=0, description="New absolute amount.")
    unit: UnitCode


def _level(ingredient_id: int, name: str, dimension, quantity_base) -> dict:
    return {
        "ingredient_id": ingredient_id,
        "name": name,
        "stock": quantity(quantity_base, dimension),
    }


def _change(change: StockChange) -> dict:
    item = change.item
    dimension = item.ingredient.dimension
    out = _level(item.ingredient_id, item.ingredient.name, dimension, item.quantity_base)
    if change.movement is not None and change.movement.shortfall_base > 0:
        out["shortfall"] = quantity(change.movement.shortfall_base, dimension)
    return out


def get_stock(ctx: ToolContext, args: GetStockArgs) -> dict:
    if args.ingredient_ids is None:
        items = [
            _level(i.ingredient_id, i.ingredient.name, i.ingredient.dimension, i.quantity_base)
            for i in stock_service.list_stock(ctx.db)
            if i.quantity_base > 0
        ]
        return {"items": items}
    items = []
    for ingredient_id in dict.fromkeys(args.ingredient_ids):
        ingredient = ingredients_service.get_ingredient(ctx.db, ingredient_id)
        qty = ingredients_service.stock_of(ctx.db, ingredient.id)
        items.append(_level(ingredient.id, ingredient.name, ingredient.dimension, qty))
    return {"items": items}


def adjust_stock(ctx: ToolContext, args: AdjustStockArgs) -> dict:
    change = stock_service.adjust_stock(
        ctx.db,
        ctx.user,
        ctx.source,
        args.ingredient_id,
        args.delta,
        args.unit,
        reason=StockReason(args.reason or StockReason.MANUAL),
    )
    return _change(change)


def preview_set_stock(ctx: ToolContext, args: SetStockArgs) -> dict:
    ingredient = ingredients_service.get_ingredient(ctx.db, args.ingredient_id)
    target = stock_service.to_ingredient_base(ingredient, args.set_to, args.unit)
    current = ingredients_service.stock_of(ctx.db, ingredient.id)
    return {
        "ingredient_id": ingredient.id,
        "name": ingredient.name,
        "stock": quantity(current, ingredient.dimension),
        "new_stock": quantity(target, ingredient.dimension),
    }


def set_stock(ctx: ToolContext, args: SetStockArgs) -> dict:
    change = stock_service.set_stock(
        ctx.db, ctx.user, ctx.source, args.ingredient_id, args.set_to, args.unit
    )
    return _change(change)


TOOLS = (
    Tool(
        name="get_stock",
        description="Current stock per ingredient (everything in stock, or the given ids).",
        args_model=GetStockArgs,
        kind="read",
        handler=get_stock,
    ),
    Tool(
        name="adjust_stock",
        description="Add to or take from an ingredient's stock; never goes below zero.",
        args_model=AdjustStockArgs,
        kind="write",
        handler=adjust_stock,
    ),
    Tool(
        name="set_stock",
        description="Overwrite an ingredient's stock with an absolute amount (a correction).",
        args_model=SetStockArgs,
        kind="write",
        handler=set_stock,
        requires_confirmation=True,
        preview=preview_set_stock,
    ),
)
