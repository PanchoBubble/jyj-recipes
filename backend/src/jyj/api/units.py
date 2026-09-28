from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from jyj.api.auth import CurrentUser
from jyj.db import get_db
from jyj.models import Unit
from jyj.schemas.units import UnitOut

router = APIRouter(prefix="/units", tags=["units"])


@router.get("", response_model=list[UnitOut])
def list_units(_: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> list[Unit]:
    return list(db.scalars(select(Unit).order_by(Unit.dimension, Unit.to_base, Unit.code)))
