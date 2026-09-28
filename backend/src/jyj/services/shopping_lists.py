"""Shopping plan preview and stored shopping lists. Shared by the REST API and chat tools.

A list is a snapshot of ``load_and_compute`` at creation time: items with something to buy,
plus informational items for lines that could not be converted and "to taste" reminders.
Every write to a list (item edits, complete, delete) locks its row first, so an edit cannot
slip in while ``complete`` is turning checked items into 'purchased' stock movements.
"""

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jyj.models import (
    Ingredient,
    ShoppingItemKind,
    ShoppingList,
    ShoppingListItem,
    ShoppingListStatus,
    StockReason,
    StockSource,
    User,
)
from jyj.services import stock as stock_service
from jyj.services.errors import ConflictError, InvalidError, NotFoundError
from jyj.shopping.aggregate import ShoppingItem, ShoppingPlan, UnconvertedLine
from jyj.shopping.loader import load_and_compute
from jyj.units import (
    UNITS,
    Dimension,
    Unconvertible,
    UnknownUnitError,
    base_unit,
    convert,
    get_unit,
)

RANGE_MAX_DAYS = 62
LIST_LIMIT_MAX = 100


def preview(db: Session, start: dt.date, end: dt.date, today: dt.date) -> ShoppingPlan:
    _check_range(start, end)
    return load_and_compute(db, start, end, today)


def create_list(
    db: Session, user: User, start: dt.date, end: dt.date, today: dt.date
) -> ShoppingList:
    plan = preview(db, start, end, today)
    shopping_list = ShoppingList(
        start_date=start, end_date=end, created_by=user.id, status=ShoppingListStatus.OPEN
    )
    check_ids = [c.ingredient_id for c in plan.check_have]
    dimensions: dict[int, Dimension] = {}
    if check_ids:
        rows = db.execute(
            select(Ingredient.id, Ingredient.dimension).where(Ingredient.id.in_(check_ids))
        )
        dimensions = {ingredient_id: dimension for ingredient_id, dimension in rows}
    shopping_list.items = snapshot_items(plan, dimensions)
    db.add(shopping_list)
    db.flush()
    return get_list(db, shopping_list.id)


def snapshot_items(
    plan: ShoppingPlan, check_have_dimensions: Mapping[int, Dimension]
) -> list[ShoppingListItem]:
    """List items for ``plan``; ``check_have_dimensions`` maps each "to taste" ingredient."""
    items: list[ShoppingListItem] = []
    for item in plan.items:
        if item.to_buy_base > 0:
            kind = ShoppingItemKind.BUY
        elif item.unconverted:
            kind = ShoppingItemKind.UNCONVERTED
        else:
            continue
        items.append(_measured_item(item, kind, len(items)))
    for check in plan.check_have:
        items.append(
            ShoppingListItem(
                ingredient_id=check.ingredient_id,
                kind=ShoppingItemKind.CHECK_HAVE,
                category=check.category,
                required_base=Decimal("0.000"),
                available_base=Decimal("0.000"),
                to_buy_base=Decimal("0.000"),
                display_unit=base_unit(check_have_dimensions[check.ingredient_id]).code,
                checked=False,
                unconverted=[],
                recipe_names=list(check.recipe_names),
                position=len(items),
            )
        )
    return items


def unconverted_json(line: UnconvertedLine) -> dict[str, Any]:
    return {
        "amount": str(line.amount),
        "unit": line.unit_code,
        "reason": line.reason.value,
        "recipe_names": list(line.recipe_names),
    }


def list_lists(db: Session, *, limit: int = 20, offset: int = 0) -> list[ShoppingList]:
    """Most recently created first."""
    return list(
        db.scalars(
            select(ShoppingList)
            .order_by(ShoppingList.created_at.desc(), ShoppingList.id.desc())
            .limit(min(limit, LIST_LIMIT_MAX))
            .offset(offset)
        )
    )


def item_counts(db: Session, list_ids: list[int]) -> dict[int, tuple[int, int]]:
    """``{list_id: (items, checked items)}``."""
    if not list_ids:
        return {}
    rows = db.execute(
        select(
            ShoppingListItem.list_id,
            func.count(),
            func.count().filter(ShoppingListItem.checked),
        )
        .where(ShoppingListItem.list_id.in_(list_ids))
        .group_by(ShoppingListItem.list_id)
    )
    return {list_id: (total, checked) for list_id, total, checked in rows}


def get_list(db: Session, list_id: int, *, lock: bool = False) -> ShoppingList:
    stmt = select(ShoppingList).where(ShoppingList.id == list_id)
    if lock:
        stmt = stmt.with_for_update(of=ShoppingList).execution_options(populate_existing=True)
    shopping_list = db.scalar(stmt)
    if shopping_list is None:
        raise NotFoundError(f"shopping list {list_id} not found")
    return shopping_list


def delete_list(db: Session, list_id: int) -> None:
    """Items go with the list; stock movements it wrote stay, unlinked (FK sets null)."""
    db.delete(get_list(db, list_id, lock=True))
    db.flush()


def update_item(
    db: Session, list_id: int, item_id: int, changes: dict[str, Any]
) -> ShoppingListItem:
    """``changes`` may hold ``checked``, ``bought_quantity`` (None clears it) and ``unit``."""
    shopping_list = _open_list(db, list_id)
    item = next((i for i in shopping_list.items if i.id == item_id), None)
    if item is None:
        raise NotFoundError(f"item {item_id} not found in shopping list {list_id}")
    if "checked" in changes:
        item.checked = changes["checked"]
    if "bought_quantity" in changes:
        amount = changes["bought_quantity"]
        item.bought_base = (
            None
            if amount is None
            else _to_item_base(item, amount, changes.get("unit") or item.display_unit)
        )
    db.flush()
    return item


def complete_list(db: Session, user: User, source: StockSource, list_id: int) -> ShoppingList:
    """Write one 'purchased' movement per checked item and mark the list done.

    Idempotent: completing a done list changes nothing. Checked items with nothing bought
    (a zero amount, or a "to taste" reminder with no bought quantity) write no movement.
    """
    shopping_list = get_list(db, list_id, lock=True)
    if shopping_list.status is ShoppingListStatus.DONE:
        return shopping_list
    for item in shopping_list.items:
        if not item.checked:
            continue
        amount = item.to_buy_base if item.bought_base is None else item.bought_base
        if amount <= 0:
            continue
        stock_service.adjust_stock(
            db,
            user,
            source,
            item.ingredient_id,
            amount,
            item_base_unit(item),
            reason=StockReason.PURCHASED,
            shopping_list_id=shopping_list.id,
        )
    shopping_list.status = ShoppingListStatus.DONE
    shopping_list.completed_at = dt.datetime.now(dt.UTC)
    db.flush()
    return shopping_list


def item_base_unit(item: ShoppingListItem) -> str:
    return base_unit(get_unit(item.display_unit).dimension).code


def _open_list(db: Session, list_id: int) -> ShoppingList:
    shopping_list = get_list(db, list_id, lock=True)
    if shopping_list.status is ShoppingListStatus.DONE:
        raise ConflictError(f"shopping list {list_id} is already completed", list_id=list_id)
    return shopping_list


def _to_item_base(item: ShoppingListItem, amount: Decimal, unit: str) -> Decimal:
    try:
        src = get_unit(unit)
    except UnknownUnitError:
        raise InvalidError(f"unknown unit {unit!r}", known_units=sorted(UNITS)) from None
    if not amount.is_finite() or amount < 0:
        raise InvalidError("bought_quantity must be a finite, non-negative number")
    dst = get_unit(item_base_unit(item))
    ingredient = item.ingredient
    result = convert(amount, src, dst, ingredient.conversions)
    if isinstance(result, Unconvertible):
        raise InvalidError(
            f"cannot convert {src.code!r} to {dst.code!r} for {ingredient.name!r}",
            reason=result.reason.value,
            unit=src.code,
            base_unit=dst.code,
        )
    if result.amount > stock_service.MAX_QUANTITY:
        raise InvalidError("bought_quantity is too large")
    return result.amount


def _check_range(start: dt.date, end: dt.date) -> None:
    if end < start:
        raise InvalidError("'to' must not be before 'from'")
    if (end - start).days + 1 > RANGE_MAX_DAYS:
        raise InvalidError(f"the range can span at most {RANGE_MAX_DAYS} days")


def _measured_item(item: ShoppingItem, kind: ShoppingItemKind, position: int) -> ShoppingListItem:
    return ShoppingListItem(
        ingredient_id=item.ingredient_id,
        kind=kind,
        category=item.category,
        required_base=item.required_base,
        available_base=item.available_base,
        to_buy_base=item.to_buy_base,
        display_unit=item.display.unit.code,
        checked=False,
        unconverted=[unconverted_json(u) for u in item.unconverted],
        recipe_names=list(
            dict.fromkeys(
                [c.meal.recipe_name for c in item.contributions]
                + [name for u in item.unconverted for name in u.recipe_names]
            )
        ),
        position=position,
    )
