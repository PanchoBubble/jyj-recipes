"""Ingredient tools: thin wrappers over ``jyj.services.ingredients``."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from jyj.chat.tools.common import Amount, MeasurableDimension, Query, UnitCode, quantity
from jyj.chat.tools.registry import Tool, ToolContext
from jyj.services import ingredients as ingredients_service

LIST_LIMIT_MAX = 200
LIST_LIMIT_DEFAULT = 50


class ListIngredientsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Query | None = Field(default=None, description="Name substring; null lists all.")
    limit: Annotated[int, Field(ge=1, le=LIST_LIMIT_MAX)] | None = None


class CreateIngredientArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    dimension: MeasurableDimension
    default_unit: UnitCode | None = None
    category: str | None = Field(default=None, max_length=50)
    grams_per_ml: Amount | None = Field(default=None, gt=0, description="Density, for volume.")
    grams_per_piece: Amount | None = Field(default=None, gt=0, description="Weight of one piece.")


def list_ingredients(ctx: ToolContext, args: ListIngredientsArgs) -> dict:
    found = ingredients_service.list_ingredients(ctx.db, args.query)
    limit = args.limit or LIST_LIMIT_DEFAULT
    return {
        "total": len(found),
        "items": [
            {
                "id": row.ingredient.id,
                "name": row.ingredient.name,
                "dimension": row.ingredient.dimension.value,
                "default_unit": row.ingredient.default_unit,
                "stock": quantity(row.quantity_base, row.ingredient.dimension),
            }
            for row in found[:limit]
        ],
    }


def create_ingredient(ctx: ToolContext, args: CreateIngredientArgs) -> dict:
    ingredient = ingredients_service.create_ingredient(
        ctx.db, ctx.user, ctx.source, **args.model_dump()
    )
    return {
        "id": ingredient.id,
        "name": ingredient.name,
        "dimension": ingredient.dimension.value,
        "default_unit": ingredient.default_unit,
    }


TOOLS = (
    Tool(
        name="list_ingredients",
        description="Ingredients with ids, dimension, default unit and current pantry amount.",
        args_model=ListIngredientsArgs,
        kind="read",
        handler=list_ingredients,
    ),
    Tool(
        name="create_ingredient",
        description="Add an ingredient to the catalogue (names are unique, case-insensitive).",
        args_model=CreateIngredientArgs,
        kind="write",
        handler=create_ingredient,
    ),
)
