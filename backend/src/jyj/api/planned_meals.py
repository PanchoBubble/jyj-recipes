import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from jyj.api.auth import CurrentUser
from jyj.db import get_db
from jyj.models import StockSource
from jyj.schemas.meal_plan import PlannedMealCreate, PlannedMealOut, PlannedMealUpdate
from jyj.services import planned_meals as service

router = APIRouter(prefix="/planned-meals", tags=["planned-meals"])

Db = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[PlannedMealOut])
def list_planned_meals(
    _: CurrentUser,
    db: Db,
    start: Annotated[dt.date, Query(alias="from")],
    end: Annotated[dt.date, Query(alias="to")],
) -> list[PlannedMealOut]:
    return [PlannedMealOut.build(m) for m in service.list_planned_meals(db, start, end)]


@router.post("", response_model=PlannedMealOut, status_code=status.HTTP_201_CREATED)
def create_planned_meal(body: PlannedMealCreate, user: CurrentUser, db: Db) -> PlannedMealOut:
    meal = service.create_planned_meal(db, user, StockSource.UI, **body.model_dump())
    return PlannedMealOut.build(meal)


@router.get("/{meal_id}", response_model=PlannedMealOut)
def get_planned_meal(meal_id: int, _: CurrentUser, db: Db) -> PlannedMealOut:
    return PlannedMealOut.build(service.get_planned_meal(db, meal_id))


@router.patch("/{meal_id}", response_model=PlannedMealOut)
def update_planned_meal(
    meal_id: int, body: PlannedMealUpdate, user: CurrentUser, db: Db
) -> PlannedMealOut:
    meal = service.update_planned_meal(
        db, user, StockSource.UI, meal_id, body.model_dump(exclude_unset=True)
    )
    return PlannedMealOut.build(meal)


@router.delete("/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_planned_meal(meal_id: int, user: CurrentUser, db: Db) -> None:
    service.delete_planned_meal(db, user, StockSource.UI, meal_id)
