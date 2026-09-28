from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jyj.models import StockItem, StockReason, StockSource
from jyj.schemas.common import AmountIn, DecimalStr
from jyj.units import Dimension, base_unit, display_quantity, quantize


class DisplayQuantity(BaseModel):
    amount: DecimalStr
    unit: str


class StockLevel(BaseModel):
    quantity_base: DecimalStr
    base_unit: str
    display: DisplayQuantity

    @classmethod
    def of(cls, dimension: Dimension, quantity_base: Decimal) -> Self:
        shown = display_quantity(quantity_base, dimension)
        return cls(
            quantity_base=quantize(quantity_base),
            base_unit=base_unit(dimension).code,
            display=DisplayQuantity(amount=shown.amount, unit=shown.unit.code),
        )


class StockItemOut(StockLevel):
    ingredient_id: int
    ingredient_name: str
    dimension: Dimension
    updated_at: datetime

    @classmethod
    def from_item(cls, item: StockItem) -> Self:
        level = StockLevel.of(item.ingredient.dimension, item.quantity_base)
        return cls(
            **level.model_dump(),
            ingredient_id=item.ingredient_id,
            ingredient_name=item.ingredient.name,
            dimension=item.ingredient.dimension,
            updated_at=item.updated_at,
        )


class StockMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient_id: int
    delta_base: DecimalStr
    shortfall_base: DecimalStr
    reason: StockReason
    source: StockSource
    planned_meal_id: int | None
    shopping_list_id: int | None
    user_id: int
    created_at: datetime


class StockMovementPage(BaseModel):
    items: list[StockMovementOut]
    total: int
    limit: int
    offset: int


class StockUpdate(BaseModel):
    """Exactly one of ``set_to`` (absolute count) or ``delta`` (signed change)."""

    model_config = ConfigDict(extra="forbid")

    set_to: AmountIn | None = Field(default=None, ge=0)
    delta: AmountIn | None = None
    unit: str = Field(min_length=1, max_length=16)
    # cooked/undo are written only by the cook flow, never directly by clients.
    reason: Literal["manual", "purchased", "correction"] | None = None

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> Self:
        if (self.set_to is None) == (self.delta is None):
            raise ValueError("provide exactly one of set_to or delta")
        return self


class StockChangeOut(BaseModel):
    stock: StockItemOut
    movement: StockMovementOut | None
