from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from jyj.api.auth import CurrentUser
from jyj.db import get_db
from jyj.models import StockReason, StockSource
from jyj.schemas.stock import (
    StockChangeOut,
    StockItemOut,
    StockMovementOut,
    StockMovementPage,
    StockUpdate,
)
from jyj.services import stock as service

router = APIRouter(prefix="/stock", tags=["stock"])

Db = Annotated[Session, Depends(get_db)]


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


@router.get("", response_model=list[StockItemOut])
def list_stock(_: CurrentUser, db: Db) -> list[StockItemOut]:
    return [StockItemOut.from_item(item) for item in service.list_stock(db)]


# Declared before /{ingredient_id} so "movements" is never parsed as an id.
@router.get("/movements", response_model=StockMovementPage)
def list_movements(
    _: CurrentUser,
    db: Db,
    ingredient_id: int | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StockMovementPage:
    page = service.list_movements(
        db,
        ingredient_id=ingredient_id,
        from_=_aware(from_),
        to=_aware(to),
        limit=limit,
        offset=offset,
    )
    return StockMovementPage(
        items=[StockMovementOut.model_validate(m) for m in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{ingredient_id}", response_model=StockChangeOut)
def update_stock(
    ingredient_id: int, body: StockUpdate, user: CurrentUser, db: Db
) -> StockChangeOut:
    if body.set_to is not None:
        change = service.set_stock(
            db,
            user,
            StockSource.UI,
            ingredient_id,
            body.set_to,
            body.unit,
            reason=StockReason(body.reason or StockReason.CORRECTION),
        )
    else:
        assert body.delta is not None  # noqa: S101
        change = service.adjust_stock(
            db,
            user,
            StockSource.UI,
            ingredient_id,
            body.delta,
            body.unit,
            reason=StockReason(body.reason or StockReason.MANUAL),
        )
    return StockChangeOut(
        stock=StockItemOut.from_item(change.item),
        movement=StockMovementOut.model_validate(change.movement) if change.movement else None,
    )
