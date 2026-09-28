"""Meal calendar tools: thin wrappers over ``jyj.services.planned_meals``."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from jyj.chat.tools.common import DateRange, Id, IsoDate, quantity
from jyj.chat.tools.registry import Tool, ToolContext, ToolRejected
from jyj.models import MealSlot, PlannedMeal
from jyj.services import meal_slots as slots_service
from jyj.services import planned_meals as meals_service
from jyj.services.planned_meals import CookResult
from jyj.services.recipes import SERVINGS_MAX

Servings = Annotated[int, Field(ge=1, le=SERVINGS_MAX)]
Slot = Annotated[
    Annotated[int, Field(ge=1, strict=True)]
    | Annotated[str, Field(min_length=1, max_length=slots_service.NAME_MAX)],
    Field(description="Meal slot name (case-insensitive, e.g. dinner) or id."),
]
Position = Annotated[int, Field(ge=0, le=10_000)]


class GetPlanArgs(DateRange):
    pass


class PlanMealArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: IsoDate
    recipe_id: Id
    slot: Slot | None = Field(default=None, description="Optional label; null for none.")
    servings: Servings | None = Field(default=None, description="Null uses the recipe default.")


class MoveMealArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meal_id: Id
    date: IsoDate = Field(description="Target day; pass the current date to reorder in place.")
    position: Position | None = Field(
        default=None,
        description=(
            "0-based place in the target day's list; past the end appends. Null appends when "
            "the day changes and keeps the current place otherwise."
        ),
    )


class SetMealSlotArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meal_id: Id
    slot: Slot | None = Field(description="New label; null removes the label.")


class SetServingsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meal_id: Id
    servings: Servings


class MealIdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meal_id: Id


def _meal(meal: PlannedMeal) -> dict:
    return {
        "meal_id": meal.id,
        "date": meal.date.isoformat(),
        "position": meal.position,
        "slot": None if meal.slot is None else meal.slot.name,
        "recipe_id": meal.recipe_id,
        "recipe": meal.recipe.name,
        "servings": meal.servings,
        "status": meal.status.value,
    }


def _resolve_slot(ctx: ToolContext, slot: int | str) -> MealSlot:
    slots = slots_service.list_slots(ctx.db)
    wanted = slot if isinstance(slot, int) else " ".join(slot.split()).lower()
    for candidate in slots:
        if (candidate.id if isinstance(slot, int) else candidate.name.lower()) == wanted:
            return candidate
    known = ", ".join(s.name for s in slots if s.active) or "none"
    raise ToolRejected("unknown_slot", f"unknown meal slot {str(slot)[:50]!r}; slots: {known}")


def _cook_result(result: CookResult) -> dict:
    stock = []
    for impact in result.stock:
        dimension = impact.ingredient.dimension
        change = {
            "ingredient_id": impact.ingredient.id,
            "name": impact.ingredient.name,
            "change": quantity(impact.delta_base, dimension),
        }
        if impact.shortfall_base > 0:
            change["shortfall"] = quantity(impact.shortfall_base, dimension)
        stock.append(change)
    return {
        "meal": _meal(result.meal),
        "changed": result.changed,
        "stock_changes": stock,
        "skipped": [
            {
                "ingredient_id": s.line.ingredient_id,
                "name": s.line.ingredient.name,
                "reason": s.reason.value,
            }
            for s in result.skipped
        ],
    }


def get_plan(ctx: ToolContext, args: GetPlanArgs) -> dict:
    meals = meals_service.list_planned_meals(ctx.db, args.start, args.end)
    return {
        "from": args.start.isoformat(),
        "to": args.end.isoformat(),
        "meals": [_meal(m) for m in meals],
    }


def plan_meal(ctx: ToolContext, args: PlanMealArgs) -> dict:
    slot = None if args.slot is None else _resolve_slot(ctx, args.slot)
    meal = meals_service.create_planned_meal(
        ctx.db,
        ctx.user,
        ctx.source,
        date=args.date,
        slot_id=None if slot is None else slot.id,
        recipe_id=args.recipe_id,
        servings=args.servings,
    )
    return _meal(meal)


def move_meal(ctx: ToolContext, args: MoveMealArgs) -> dict:
    changes: dict = {"date": args.date}
    if args.position is not None:
        changes["position"] = args.position
    meal = meals_service.update_planned_meal(ctx.db, ctx.user, ctx.source, args.meal_id, changes)
    return _meal(meal)


def set_meal_slot(ctx: ToolContext, args: SetMealSlotArgs) -> dict:
    slot_id = None if args.slot is None else _resolve_slot(ctx, args.slot).id
    meal = meals_service.update_planned_meal(
        ctx.db, ctx.user, ctx.source, args.meal_id, {"slot_id": slot_id}
    )
    return _meal(meal)


def set_servings(ctx: ToolContext, args: SetServingsArgs) -> dict:
    meal = meals_service.update_planned_meal(
        ctx.db, ctx.user, ctx.source, args.meal_id, {"servings": args.servings}
    )
    return _meal(meal)


def preview_remove_meal(ctx: ToolContext, args: MealIdArgs) -> dict:
    return _meal(meals_service.get_planned_meal(ctx.db, args.meal_id))


def remove_meal(ctx: ToolContext, args: MealIdArgs) -> dict:
    meals_service.delete_planned_meal(ctx.db, ctx.user, ctx.source, args.meal_id)
    return {"meal_id": args.meal_id, "removed": True}


def mark_cooked(ctx: ToolContext, args: MealIdArgs) -> dict:
    return _cook_result(meals_service.cook(ctx.db, ctx.user, ctx.source, args.meal_id))


def uncook_meal(ctx: ToolContext, args: MealIdArgs) -> dict:
    return _cook_result(meals_service.uncook(ctx.db, ctx.user, ctx.source, args.meal_id))


TOOLS = (
    Tool(
        name="get_plan",
        description=(
            "Planned meals between two ISO dates (at most 31 days), in calendar order: by date, "
            "then position within the day. slot is an optional label (null when unlabelled)."
        ),
        args_model=GetPlanArgs,
        kind="read",
        handler=get_plan,
    ),
    Tool(
        name="plan_meal",
        description=(
            "Add a recipe to the end of a date's meals, optionally labelled with a slot; null "
            "servings use the recipe default."
        ),
        args_model=PlanMealArgs,
        kind="write",
        handler=plan_meal,
    ),
    Tool(
        name="move_meal",
        description="Move a planned meal to another date and/or place in the day's list.",
        args_model=MoveMealArgs,
        kind="write",
        handler=move_meal,
    ),
    Tool(
        name="set_meal_slot",
        description="Set or remove (null) the slot label of a planned meal; order is unchanged.",
        args_model=SetMealSlotArgs,
        kind="write",
        handler=set_meal_slot,
    ),
    Tool(
        name="set_servings",
        description="Change how many people a planned meal is for (not allowed once cooked).",
        args_model=SetServingsArgs,
        kind="write",
        handler=set_servings,
    ),
    Tool(
        name="remove_meal",
        description="Take a planned meal off the calendar (cooked meals must be uncooked first).",
        args_model=MealIdArgs,
        kind="write",
        handler=remove_meal,
        requires_confirmation=True,
        preview=preview_remove_meal,
    ),
    Tool(
        name="mark_cooked",
        description=(
            "Mark a meal cooked. This DEDUCTS its ingredients from stock; the result lists "
            "every stock change, shortfalls and skipped lines. Tell the user what changed."
        ),
        args_model=MealIdArgs,
        kind="write",
        handler=mark_cooked,
    ),
    Tool(
        name="uncook_meal",
        description="Undo mark_cooked: the meal goes back to planned and its stock is returned.",
        args_model=MealIdArgs,
        kind="write",
        handler=uncook_meal,
    ),
)
