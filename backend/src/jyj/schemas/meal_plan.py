import datetime as dt
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from jyj.models import PlannedMeal, PlannedMealStatus
from jyj.schemas.common import DecimalStr
from jyj.schemas.stock import DisplayQuantity
from jyj.services.meal_slots import NAME_MAX
from jyj.services.photos import photo_url, thumb_url
from jyj.services.planned_meals import CookResult, SkippedLine, SkipReason, StockImpact
from jyj.services.recipes import SERVINGS_MAX
from jyj.units import base_unit, display_quantity

SlotName = Annotated[str, Field(min_length=1, max_length=NAME_MAX)]
Servings = Annotated[int, Field(ge=1, le=SERVINGS_MAX)]
Position = Annotated[int, Field(ge=0, le=10_000)]


class MealSlotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SlotName
    active: bool = True


class MealSlotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SlotName | None = None
    active: bool | None = None


class MealSlotOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: Annotated[list[int], Field(max_length=100)]


class MealSlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    position: int
    active: bool


class PlannedMealCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date
    slot_id: int
    recipe_id: int
    # Defaults to the recipe's default_servings.
    servings: Servings | None = None


class PlannedMealUpdate(BaseModel):
    """Partial update; date/slot_id/position move the meal (drag and drop)."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date | None = None
    slot_id: int | None = None
    servings: Servings | None = None
    # Index within the target cell; past the end appends.
    position: Position | None = None
    # 'cooked' only via POST /{id}/cook, which also updates stock.
    status: Literal["planned", "skipped"] | None = None


class PlannedRecipeOut(BaseModel):
    id: int
    name: str
    photo_url: str | None
    photo_thumb_url: str | None
    default_servings: int
    archived_at: dt.datetime | None


class PlannedMealOut(BaseModel):
    id: int
    date: dt.date
    slot_id: int
    recipe_id: int
    servings: int
    position: int
    status: PlannedMealStatus
    cooked_at: dt.datetime | None
    cooked_by: int | None
    created_by: int
    created_at: dt.datetime
    updated_at: dt.datetime
    recipe: PlannedRecipeOut
    slot: MealSlotOut

    @classmethod
    def build(cls, meal: PlannedMeal) -> Self:
        recipe = meal.recipe
        return cls(
            id=meal.id,
            date=meal.date,
            slot_id=meal.slot_id,
            recipe_id=meal.recipe_id,
            servings=meal.servings,
            position=meal.position,
            status=meal.status,
            cooked_at=meal.cooked_at,
            cooked_by=meal.cooked_by,
            created_by=meal.created_by,
            created_at=meal.created_at,
            updated_at=meal.updated_at,
            recipe=PlannedRecipeOut(
                id=recipe.id,
                name=recipe.name,
                photo_url=photo_url(recipe.photo_path),
                photo_thumb_url=thumb_url(recipe.photo_path),
                default_servings=recipe.default_servings,
                archived_at=recipe.archived_at,
            ),
            slot=MealSlotOut.model_validate(meal.slot),
        )


class StockImpactOut(BaseModel):
    ingredient_id: int
    name: str
    delta_base: DecimalStr
    shortfall_base: DecimalStr
    base_unit: str
    display: DisplayQuantity

    @classmethod
    def build(cls, impact: StockImpact) -> Self:
        dimension = impact.ingredient.dimension
        shown = display_quantity(impact.delta_base, dimension)
        return cls(
            ingredient_id=impact.ingredient.id,
            name=impact.ingredient.name,
            delta_base=impact.delta_base,
            shortfall_base=impact.shortfall_base,
            base_unit=base_unit(dimension).code,
            display=DisplayQuantity(amount=shown.amount, unit=shown.unit.code),
        )


class SkippedLineOut(BaseModel):
    recipe_ingredient_id: int
    ingredient_id: int
    name: str
    # amount_per_person x servings in the line's unit; null for to-taste lines.
    amount: DecimalStr | None
    unit: str
    reason: SkipReason

    @classmethod
    def build(cls, skipped: SkippedLine) -> Self:
        line = skipped.line
        return cls(
            recipe_ingredient_id=line.id,
            ingredient_id=line.ingredient_id,
            name=line.ingredient.name,
            amount=skipped.amount,
            unit=line.unit_code,
            reason=skipped.reason,
        )


class CookOut(BaseModel):
    meal: PlannedMealOut
    # False when the meal was already in the requested state and nothing was written.
    changed: bool
    stock: list[StockImpactOut]
    skipped: list[SkippedLineOut]

    @classmethod
    def build(cls, result: CookResult) -> Self:
        return cls(
            meal=PlannedMealOut.build(result.meal),
            changed=result.changed,
            stock=[StockImpactOut.build(i) for i in result.stock],
            skipped=[SkippedLineOut.build(s) for s in result.skipped],
        )
