from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from jyj.api.auth import CurrentUser
from jyj.db import get_db
from jyj.models import StockSource
from jyj.schemas.ingredients import IngredientCreate, IngredientOut, IngredientUpdate
from jyj.services import ingredients as service

router = APIRouter(prefix="/ingredients", tags=["ingredients"])

Db = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[IngredientOut])
def list_ingredients(
    _: CurrentUser, db: Db, q: Annotated[str | None, Query(max_length=100)] = None
) -> list[IngredientOut]:
    return [
        IngredientOut.build(r.ingredient, r.quantity_base) for r in service.list_ingredients(db, q)
    ]


@router.post("", response_model=IngredientOut, status_code=status.HTTP_201_CREATED)
def create_ingredient(body: IngredientCreate, user: CurrentUser, db: Db) -> IngredientOut:
    ingredient = service.create_ingredient(db, user, StockSource.UI, **body.model_dump())
    return IngredientOut.build(ingredient, service.stock_of(db, ingredient.id))


@router.get("/{ingredient_id}", response_model=IngredientOut)
def get_ingredient(ingredient_id: int, _: CurrentUser, db: Db) -> IngredientOut:
    ingredient = service.get_ingredient(db, ingredient_id)
    return IngredientOut.build(ingredient, service.stock_of(db, ingredient.id))


@router.patch("/{ingredient_id}", response_model=IngredientOut)
def update_ingredient(
    ingredient_id: int, body: IngredientUpdate, user: CurrentUser, db: Db
) -> IngredientOut:
    ingredient = service.update_ingredient(
        db, user, StockSource.UI, ingredient_id, body.model_dump(exclude_unset=True)
    )
    return IngredientOut.build(ingredient, service.stock_of(db, ingredient.id))


@router.delete("/{ingredient_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ingredient(ingredient_id: int, user: CurrentUser, db: Db) -> None:
    service.delete_ingredient(db, user, StockSource.UI, ingredient_id)
