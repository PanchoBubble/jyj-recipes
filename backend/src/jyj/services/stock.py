"""Stock balance and its append-only ledger. Shared by the REST API and chat tools.

Every change locks the ingredient's stock_items row (creating it if missing), appends one
stock_movements row and updates the balance in the caller's transaction, so concurrent
adjustments serialise and ``sum(delta_base) == quantity_base`` always holds.

Stock floors at 0: a withdrawal larger than the balance applies only what is there and
records the rest as ``shortfall_base`` on the movement.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from jyj.models import Ingredient, StockItem, StockMovement, StockReason, StockSource, User
from jyj.services.errors import InvalidError
from jyj.services.ingredients import get_ingredient
from jyj.units import (
    UNITS,
    Unconvertible,
    UnknownUnitError,
    base_unit,
    convert,
    get_unit,
)

# numeric(12,3)
MAX_QUANTITY = Decimal("999999999.999")
ZERO = Decimal("0.000")


@dataclass(frozen=True, slots=True)
class StockChange:
    item: StockItem
    movement: StockMovement | None


@dataclass(frozen=True, slots=True)
class MovementPage:
    items: list[StockMovement]
    total: int


def list_stock(db: Session) -> list[StockItem]:
    return list(
        db.scalars(
            select(StockItem)
            .join(Ingredient, Ingredient.id == StockItem.ingredient_id)
            .order_by(func.lower(Ingredient.name), Ingredient.id)
        )
    )


def to_ingredient_base(ingredient: Ingredient, amount: Decimal, unit: str) -> Decimal:
    """``amount`` of ``unit`` in the ingredient's base unit, or a 422 ``InvalidError``."""
    try:
        src = get_unit(unit)
    except UnknownUnitError:
        raise InvalidError(f"unknown unit {unit!r}", known_units=sorted(UNITS)) from None
    if not amount.is_finite():
        raise InvalidError("amount must be a finite number")
    result = convert(amount, src, base_unit(ingredient.dimension), ingredient.conversions)
    if isinstance(result, Unconvertible):
        raise InvalidError(
            f"cannot convert {src.code!r} to {result.to_unit.code!r} for {ingredient.name!r}",
            reason=result.reason.value,
            unit=src.code,
            base_unit=result.to_unit.code,
        )
    if abs(result.amount) > MAX_QUANTITY:
        raise InvalidError("amount is too large")
    return result.amount


def adjust_stock(
    db: Session,
    user: User,
    source: StockSource,
    ingredient_id: int,
    delta: Decimal,
    unit: str,
    *,
    reason: StockReason = StockReason.MANUAL,
    planned_meal_id: int | None = None,
    shopping_list_id: int | None = None,
) -> StockChange:
    ingredient = get_ingredient(db, ingredient_id)
    delta_base = to_ingredient_base(ingredient, delta, unit)
    if delta_base == 0:
        raise InvalidError("delta rounds to zero in the ingredient's base unit")
    item = _lock_item(db, ingredient.id)
    return _apply(
        db,
        user,
        source,
        item,
        delta_base,
        reason,
        planned_meal_id=planned_meal_id,
        shopping_list_id=shopping_list_id,
    )


def set_stock(
    db: Session,
    user: User,
    source: StockSource,
    ingredient_id: int,
    set_to: Decimal,
    unit: str,
    *,
    reason: StockReason = StockReason.CORRECTION,
) -> StockChange:
    ingredient = get_ingredient(db, ingredient_id)
    if set_to < 0:
        raise InvalidError("set_to must not be negative")
    target = to_ingredient_base(ingredient, set_to, unit)
    item = _lock_item(db, ingredient.id)
    delta_base = target - item.quantity_base
    if delta_base == 0:
        return StockChange(item, None)
    return _apply(db, user, source, item, delta_base, reason)


def list_movements(
    db: Session,
    *,
    ingredient_id: int | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> MovementPage:
    """Newest first. ``from_`` is inclusive, ``to`` exclusive."""
    filters = []
    if ingredient_id is not None:
        filters.append(StockMovement.ingredient_id == ingredient_id)
    if from_ is not None:
        filters.append(StockMovement.created_at >= from_)
    if to is not None:
        filters.append(StockMovement.created_at < to)
    total = db.scalar(select(func.count()).select_from(StockMovement).where(*filters)) or 0
    items = db.scalars(
        select(StockMovement)
        .where(*filters)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return MovementPage(list(items), total)


def _lock_item(db: Session, ingredient_id: int) -> StockItem:
    # ON CONFLICT DO NOTHING lets two first-ever adjustments race without a PK violation;
    # the loser then blocks on FOR UPDATE until the winner commits.
    db.execute(
        insert(StockItem)
        .values(ingredient_id=ingredient_id, quantity_base=ZERO)
        .on_conflict_do_nothing(index_elements=[StockItem.ingredient_id])
    )
    return db.scalars(
        select(StockItem)
        .where(StockItem.ingredient_id == ingredient_id)
        .with_for_update(of=StockItem)
        .execution_options(populate_existing=True)
    ).one()


def _apply(
    db: Session,
    user: User,
    source: StockSource,
    item: StockItem,
    requested: Decimal,
    reason: StockReason,
    *,
    planned_meal_id: int | None = None,
    shopping_list_id: int | None = None,
) -> StockChange:
    current = item.quantity_base
    new = current + requested
    shortfall = ZERO
    if new < 0:
        shortfall = -new
        new = ZERO
    if new > MAX_QUANTITY:
        raise InvalidError("resulting stock is too large")
    movement = StockMovement(
        ingredient_id=item.ingredient_id,
        delta_base=new - current,
        reason=reason,
        shortfall_base=shortfall,
        planned_meal_id=planned_meal_id,
        shopping_list_id=shopping_list_id,
        user_id=user.id,
        source=source,
    )
    item.quantity_base = new
    db.add(movement)
    db.flush()
    db.refresh(item)
    db.refresh(movement)
    return StockChange(item, movement)
