"""Planned meals: recipes placed in (date, slot) cells of the meal calendar.

Within a cell, meals are ordered by a dense 0-based ``position``. Every write to a cell locks
its slot row first, so concurrent adds and moves into the same slot serialise and positions
stay compact.

Cooking deducts the recipe (scaled by servings) from stock through the stock ledger; uncooking
writes 'undo' movements that reverse exactly what that cook applied. Writes to an existing
meal lock its row first (then slots, then stock items by ingredient id), so cook, uncook,
edits and deletes of the same meal serialise.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, lazyload

from jyj.models import (
    Ingredient,
    MealSlot,
    PlannedMeal,
    PlannedMealStatus,
    Recipe,
    RecipeIngredient,
    StockMovement,
    StockReason,
    StockSource,
    User,
)
from jyj.services import meal_slots as slots_service
from jyj.services import recipes as recipes_service
from jyj.services import stock as stock_service
from jyj.services.errors import ConflictError, InvalidError, NotFoundError
from jyj.units import Dimension, Unconvertible, base_unit, convert, get_unit, quantize

RANGE_MAX_DAYS = 62
UPDATABLE_FIELDS = frozenset({"date", "slot_id", "servings", "position", "status"})
ZERO = Decimal("0.000")


class SkipReason(StrEnum):
    TO_TASTE = "to_taste"
    DIMENSIONLESS = "dimensionless"
    MISSING_GRAMS_PER_ML = "missing_grams_per_ml"
    MISSING_GRAMS_PER_PIECE = "missing_grams_per_piece"
    ROUNDS_TO_ZERO = "rounds_to_zero"


@dataclass(frozen=True, slots=True)
class StockImpact:
    ingredient: Ingredient
    delta_base: Decimal
    shortfall_base: Decimal


@dataclass(frozen=True, slots=True)
class SkippedLine:
    line: RecipeIngredient
    amount: Decimal | None
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class CookResult:
    meal: PlannedMeal
    # False when the call was a no-op because the meal was already in the target state.
    changed: bool
    stock: list[StockImpact]
    skipped: list[SkippedLine]


def _query():
    # The calendar only renders name/photo; skip the per-recipe ingredient load.
    return select(PlannedMeal).options(lazyload(PlannedMeal.recipe, Recipe.ingredients))


def list_planned_meals(db: Session, start: dt.date, end: dt.date) -> list[PlannedMeal]:
    """Meals with ``start <= date <= end``, in calendar order (date, slot order, position)."""
    if end < start:
        raise InvalidError("'to' must not be before 'from'")
    if (end - start).days + 1 > RANGE_MAX_DAYS:
        raise InvalidError(f"the range can span at most {RANGE_MAX_DAYS} days")
    stmt = (
        _query()
        .join(PlannedMeal.slot)
        .where(PlannedMeal.date.between(start, end))
        .order_by(PlannedMeal.date, MealSlot.position, MealSlot.id, PlannedMeal.position)
    )
    return list(db.scalars(stmt).unique())


def get_planned_meal(db: Session, meal_id: int, *, lock: bool = False) -> PlannedMeal:
    stmt = _query().where(PlannedMeal.id == meal_id)
    if lock:
        stmt = stmt.with_for_update(of=PlannedMeal).execution_options(populate_existing=True)
    meal = db.scalar(stmt)
    if meal is None:
        raise NotFoundError(f"planned meal {meal_id} not found")
    return meal


def create_planned_meal(
    db: Session,
    user: User,
    source: StockSource,
    *,
    date: dt.date,
    slot_id: int,
    recipe_id: int,
    servings: int | None = None,
) -> PlannedMeal:
    slot = slots_service.get_slot(db, slot_id, lock=True)
    if not slot.active:
        raise InvalidError(f"meal slot {slot.name!r} is inactive", slot_id=slot.id)
    recipe = recipes_service.get_recipe(db, recipe_id)
    if recipe.archived_at is not None:
        raise InvalidError(f"recipe {recipe.name!r} is archived", recipe_id=recipe.id)

    date = _clean_date(date)
    cell = _cell(db, date, slot.id)
    meal = PlannedMeal(
        date=date,
        slot_id=slot.id,
        recipe_id=recipe.id,
        servings=_clean_servings(recipe.default_servings if servings is None else servings),
        position=len(cell),
        status=PlannedMealStatus.PLANNED,
        created_by=user.id,
    )
    db.add(meal)
    db.flush()
    return _reload(db, meal)


def update_planned_meal(
    db: Session, user: User, source: StockSource, meal_id: int, changes: Mapping[str, Any]
) -> PlannedMeal:
    """Partial update. Changing date/slot/position moves the meal: it is inserted at
    ``position`` in the target cell (appended when omitted) and both cells are renumbered."""
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise InvalidError(f"unknown fields: {', '.join(sorted(unknown))}")
    nulled = sorted(f for f in UPDATABLE_FIELDS & set(changes) if changes[f] is None)
    if nulled:
        raise InvalidError(f"fields cannot be null: {', '.join(nulled)}")

    meal = get_planned_meal(db, meal_id, lock=True)
    target_date = _clean_date(changes["date"]) if "date" in changes else meal.date
    target_slot_id = changes.get("slot_id", meal.slot_id)
    moving_cell = (target_date, target_slot_id) != (meal.date, meal.slot_id)

    for locked_id in sorted({meal.slot_id, target_slot_id}):
        slot = slots_service.get_slot(db, locked_id, lock=True)
        if locked_id == target_slot_id and moving_cell and not slot.active:
            raise InvalidError(f"meal slot {slot.name!r} is inactive", slot_id=slot.id)

    if "servings" in changes:
        servings = _clean_servings(changes["servings"])
        if servings != meal.servings and meal.status is PlannedMealStatus.COOKED:
            raise ConflictError(
                "cannot change servings of a cooked meal; uncook it first",
                status_value=meal.status.value,
            )
        meal.servings = servings

    if "status" in changes:
        _set_plain_status(meal, changes["status"])

    if moving_cell or "position" in changes:
        position = _clean_position(changes["position"]) if "position" in changes else None
        if moving_cell:
            _renumber([m for m in _cell(db, meal.date, meal.slot_id) if m.id != meal.id])
        target = [m for m in _cell(db, target_date, target_slot_id) if m.id != meal.id]
        index = len(target) if position is None else min(position, len(target))
        target.insert(index, meal)
        meal.date = target_date
        meal.slot_id = target_slot_id
        _renumber(target)

    db.flush()
    return _reload(db, meal)


def delete_planned_meal(db: Session, user: User, source: StockSource, meal_id: int) -> None:
    meal = get_planned_meal(db, meal_id, lock=True)
    slots_service.get_slot(db, meal.slot_id, lock=True)
    if meal.status is PlannedMealStatus.COOKED:
        raise ConflictError(
            "cooked meals cannot be deleted; uncook it first", status_value=meal.status.value
        )
    date, slot_id = meal.date, meal.slot_id
    db.delete(meal)
    db.flush()
    _renumber(_cell(db, date, slot_id))
    db.flush()


def cook(db: Session, user: User, source: StockSource, meal_id: int) -> CookResult:
    """Mark the meal cooked and deduct its recipe from stock, floored at 0.

    Lines with no measurable amount, or whose unit cannot be converted to the ingredient's
    base unit, are skipped and reported rather than guessed. Cooking an already cooked meal
    is a no-op that reports the stock impact of the cook that is in effect.
    """
    meal = get_planned_meal(db, meal_id, lock=True)
    needed, skipped = _requirements(meal)
    if meal.status is PlannedMealStatus.COOKED:
        return CookResult(meal, False, _impacts(db, _active_cook_movements(db, meal.id)), skipped)

    movements = [
        stock_service.apply_base_delta(
            db,
            user,
            source,
            ingredient_id,
            -amount,
            StockReason.COOKED,
            planned_meal_id=meal.id,
        ).movement
        for ingredient_id, amount in sorted(needed.items())
    ]
    meal.status = PlannedMealStatus.COOKED
    meal.cooked_at = func.now()
    meal.cooked_by = user.id
    db.flush()
    return CookResult(_reload(db, meal), True, _impacts(db, movements), skipped)


def uncook(db: Session, user: User, source: StockSource, meal_id: int) -> CookResult:
    """Return a cooked meal to planned, giving back exactly what its cook deducted.

    Undo movements mirror the applied (post-floor) deltas, not the recipe's nominal amounts,
    so stock ends where it would be had the meal never been cooked, minus any edits made in
    between. A meal that is not cooked is left as is.
    """
    meal = get_planned_meal(db, meal_id, lock=True)
    if meal.status is not PlannedMealStatus.COOKED:
        return CookResult(meal, False, [], [])

    applied: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for movement in _active_cook_movements(db, meal.id):
        applied[movement.ingredient_id] += movement.delta_base
    movements = [
        stock_service.apply_base_delta(
            db,
            user,
            source,
            ingredient_id,
            -delta,
            StockReason.UNDO,
            planned_meal_id=meal.id,
        ).movement
        for ingredient_id, delta in sorted(applied.items())
        if delta != 0
    ]
    meal.status = PlannedMealStatus.PLANNED
    meal.cooked_at = None
    meal.cooked_by = None
    db.flush()
    return CookResult(_reload(db, meal), True, _impacts(db, movements), [])


def _requirements(meal: PlannedMeal) -> tuple[dict[int, Decimal], list[SkippedLine]]:
    """Total base amount per ingredient for the meal's servings, plus the lines left out."""
    needed: dict[int, Decimal] = defaultdict(lambda: ZERO)
    skipped: list[SkippedLine] = []
    for line in meal.recipe.ingredients:
        ingredient = line.ingredient
        unit = get_unit(line.unit_code)
        amount = None
        if line.amount_per_person is not None:
            amount = quantize(line.amount_per_person * meal.servings)
        if unit.dimension is Dimension.NONE or amount is None:
            to_taste = amount is None or unit.code == "to_taste"
            reason = SkipReason.TO_TASTE if to_taste else SkipReason.DIMENSIONLESS
            skipped.append(SkippedLine(line, amount, reason))
            continue
        result = convert(amount, unit, base_unit(ingredient.dimension), ingredient.conversions)
        if isinstance(result, Unconvertible):
            skipped.append(SkippedLine(line, amount, SkipReason(result.reason.value)))
            continue
        if result.amount == 0:
            skipped.append(SkippedLine(line, amount, SkipReason.ROUNDS_TO_ZERO))
            continue
        needed[ingredient.id] += result.amount
    for ingredient_id, total in needed.items():
        if total > stock_service.MAX_QUANTITY:
            raise InvalidError("recipe amount is too large", ingredient_id=ingredient_id)
    return dict(needed), skipped


def _active_cook_movements(db: Session, meal_id: int) -> list[StockMovement]:
    """'cooked' movements of the meal's current cook: those after its latest undo.

    The meal row lock serialises cook and uncook, so ids order the cycles.
    """
    last_undo = (
        select(func.coalesce(func.max(StockMovement.id), 0))
        .where(StockMovement.planned_meal_id == meal_id, StockMovement.reason == StockReason.UNDO)
        .scalar_subquery()
    )
    return list(
        db.scalars(
            select(StockMovement)
            .where(
                StockMovement.planned_meal_id == meal_id,
                StockMovement.reason == StockReason.COOKED,
                StockMovement.id > last_undo,
            )
            .order_by(StockMovement.ingredient_id, StockMovement.id)
        )
    )


def _impacts(db: Session, movements: Sequence[StockMovement | None]) -> list[StockImpact]:
    """Per-ingredient totals of ``movements``, in ingredient id order."""
    totals: dict[int, tuple[Decimal, Decimal]] = {}
    for m in movements:
        if m is None:
            continue
        delta, shortfall = totals.get(m.ingredient_id, (ZERO, ZERO))
        totals[m.ingredient_id] = (delta + m.delta_base, shortfall + m.shortfall_base)
    if not totals:
        return []
    ingredients = db.scalars(
        select(Ingredient).where(Ingredient.id.in_(totals)).order_by(Ingredient.id)
    )
    return [StockImpact(i, *totals[i.id]) for i in ingredients]


def _set_plain_status(meal: PlannedMeal, value: Any) -> None:
    try:
        status = PlannedMealStatus(value)
    except ValueError:
        raise InvalidError("status must be 'planned' or 'skipped'") from None
    if status is meal.status:
        return
    if status is PlannedMealStatus.COOKED:
        raise InvalidError("use the cook action to mark a meal cooked")
    if meal.status is PlannedMealStatus.COOKED:
        raise ConflictError(
            "cannot change the status of a cooked meal; uncook it first",
            status_value=meal.status.value,
        )
    meal.status = status


def _cell(db: Session, date: dt.date, slot_id: int) -> list[PlannedMeal]:
    stmt = (
        _query()
        .where(PlannedMeal.date == date, PlannedMeal.slot_id == slot_id)
        .order_by(PlannedMeal.position, PlannedMeal.id)
    )
    return list(db.scalars(stmt).unique())


def _renumber(meals: Sequence[PlannedMeal]) -> None:
    for position, meal in enumerate(meals):
        if meal.position != position:
            meal.position = position


def _reload(db: Session, meal: PlannedMeal) -> PlannedMeal:
    db.refresh(meal)
    return meal


def _clean_date(value: dt.date) -> dt.date:
    if isinstance(value, dt.datetime) or not isinstance(value, dt.date):
        raise InvalidError("date must be a calendar date")
    return value


def _clean_servings(servings: int) -> int:
    if isinstance(servings, bool) or not isinstance(servings, int):
        raise InvalidError("servings must be an integer")
    if not 1 <= servings <= recipes_service.SERVINGS_MAX:
        raise InvalidError(f"servings must be between 1 and {recipes_service.SERVINGS_MAX}")
    return servings


def _clean_position(position: int) -> int:
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        raise InvalidError("position must be a non-negative integer")
    return position
