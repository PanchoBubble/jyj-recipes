"""Planned meals: recipes placed in (date, slot) cells of the meal calendar.

Within a cell, meals are ordered by a dense 0-based ``position``. Every write to a cell locks
its slot row first, so concurrent adds and moves into the same slot serialise and positions
stay compact.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from jyj.models import MealSlot, PlannedMeal, PlannedMealStatus, Recipe, StockSource, User
from jyj.services import meal_slots as slots_service
from jyj.services import recipes as recipes_service
from jyj.services.errors import ConflictError, InvalidError, NotFoundError

RANGE_MAX_DAYS = 62
UPDATABLE_FIELDS = frozenset({"date", "slot_id", "servings", "position"})


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


def get_planned_meal(db: Session, meal_id: int) -> PlannedMeal:
    meal = db.scalar(_query().where(PlannedMeal.id == meal_id))
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

    meal = get_planned_meal(db, meal_id)
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
    meal = get_planned_meal(db, meal_id)
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
