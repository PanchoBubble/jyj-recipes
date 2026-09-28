import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from jyj.api.auth import CurrentUser
from jyj.db import get_db
from jyj.models import StockSource
from jyj.schemas.shopping import (
    ShoppingListItemOut,
    ShoppingListItemUpdate,
    ShoppingListOut,
    ShoppingListSummary,
    ShoppingPreviewOut,
    ShoppingRange,
)
from jyj.services import shopping_lists as service

preview_router = APIRouter(prefix="/shopping", tags=["shopping"])
router = APIRouter(prefix="/shopping-lists", tags=["shopping"])

Db = Annotated[Session, Depends(get_db)]


def get_today() -> dt.date:
    return dt.date.today()


Today = Annotated[dt.date, Depends(get_today)]


@preview_router.post("/preview", response_model=ShoppingPreviewOut)
def preview(body: ShoppingRange, _: CurrentUser, db: Db, today: Today) -> ShoppingPreviewOut:
    return ShoppingPreviewOut.build(service.preview(db, body.start, body.end, today))


@router.post("", response_model=ShoppingListOut, status_code=status.HTTP_201_CREATED)
def create_list(body: ShoppingRange, user: CurrentUser, db: Db, today: Today) -> ShoppingListOut:
    return ShoppingListOut.build(service.create_list(db, user, body.start, body.end, today))


@router.get("", response_model=list[ShoppingListSummary])
def list_lists(
    _: CurrentUser,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=service.LIST_LIMIT_MAX)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ShoppingListSummary]:
    lists = service.list_lists(db, limit=limit, offset=offset)
    counts = service.item_counts(db, [s.id for s in lists])
    return [ShoppingListSummary.build(s, *counts.get(s.id, (0, 0))) for s in lists]


@router.get("/{list_id}", response_model=ShoppingListOut)
def get_list(list_id: int, _: CurrentUser, db: Db) -> ShoppingListOut:
    return ShoppingListOut.build(service.get_list(db, list_id))


@router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_list(list_id: int, _: CurrentUser, db: Db) -> None:
    service.delete_list(db, list_id)


@router.patch("/{list_id}/items/{item_id}", response_model=ShoppingListItemOut)
def update_item(
    list_id: int, item_id: int, body: ShoppingListItemUpdate, _: CurrentUser, db: Db
) -> ShoppingListItemOut:
    item = service.update_item(db, list_id, item_id, body.model_dump(exclude_unset=True))
    return ShoppingListItemOut.build(item)


@router.post("/{list_id}/complete", response_model=ShoppingListOut)
def complete_list(list_id: int, user: CurrentUser, db: Db) -> ShoppingListOut:
    return ShoppingListOut.build(service.complete_list(db, user, StockSource.UI, list_id))
