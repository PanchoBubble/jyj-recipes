from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from jyj.api.auth import CurrentUser
from jyj.db import get_db
from jyj.models import StockSource
from jyj.schemas.meal_plan import MealSlotCreate, MealSlotOrder, MealSlotOut, MealSlotUpdate
from jyj.services import meal_slots as service

router = APIRouter(prefix="/meal-slots", tags=["meal-slots"])

Db = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[MealSlotOut])
def list_slots(_: CurrentUser, db: Db) -> list[MealSlotOut]:
    return [MealSlotOut.model_validate(s) for s in service.list_slots(db)]


@router.post("", response_model=MealSlotOut, status_code=status.HTTP_201_CREATED)
def create_slot(body: MealSlotCreate, user: CurrentUser, db: Db) -> MealSlotOut:
    slot = service.create_slot(db, user, StockSource.UI, **body.model_dump())
    return MealSlotOut.model_validate(slot)


@router.put("/order", response_model=list[MealSlotOut])
def reorder_slots(body: MealSlotOrder, user: CurrentUser, db: Db) -> list[MealSlotOut]:
    slots = service.reorder_slots(db, user, StockSource.UI, body.ids)
    return [MealSlotOut.model_validate(s) for s in slots]


@router.patch("/{slot_id}", response_model=MealSlotOut)
def update_slot(slot_id: int, body: MealSlotUpdate, user: CurrentUser, db: Db) -> MealSlotOut:
    slot = service.update_slot(
        db, user, StockSource.UI, slot_id, body.model_dump(exclude_unset=True)
    )
    return MealSlotOut.model_validate(slot)


@router.delete("/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_slot(slot_id: int, user: CurrentUser, db: Db) -> None:
    service.delete_slot(db, user, StockSource.UI, slot_id)
