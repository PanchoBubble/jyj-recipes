import datetime as dt
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field

from jyj.models import PlannedMeal, PlannedMealStatus
from jyj.services.meal_slots import NAME_MAX
from jyj.services.recipes import SERVINGS_MAX

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


class PlannedRecipeOut(BaseModel):
    id: int
    name: str
    photo_path: str | None
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
                photo_path=recipe.photo_path,
                default_servings=recipe.default_servings,
                archived_at=recipe.archived_at,
            ),
            slot=MealSlotOut.model_validate(meal.slot),
        )
