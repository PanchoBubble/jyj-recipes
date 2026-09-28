from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from jyj.models import Ingredient
from jyj.schemas.common import DecimalStr
from jyj.schemas.stock import StockLevel
from jyj.units import Dimension

Name = Annotated[str, Field(min_length=1, max_length=100)]
Category = Annotated[str, Field(max_length=50)]
Factor = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False)]
MeasurableDimension = Literal["mass", "volume", "count"]


class IngredientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    dimension: MeasurableDimension
    default_unit: str | None = Field(default=None, min_length=1, max_length=16)
    category: Category | None = None
    grams_per_ml: Factor | None = None
    grams_per_piece: Factor | None = None


class IngredientUpdate(BaseModel):
    """Partial update: only fields present in the body change; null clears optional ones."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    dimension: MeasurableDimension | None = None
    default_unit: str | None = Field(default=None, min_length=1, max_length=16)
    category: Category | None = None
    grams_per_ml: Factor | None = None
    grams_per_piece: Factor | None = None


class IngredientOut(BaseModel):
    id: int
    name: str
    dimension: Dimension
    default_unit: str
    category: str | None
    grams_per_ml: DecimalStr | None
    grams_per_piece: DecimalStr | None
    stock: StockLevel
    created_at: datetime
    updated_at: datetime

    @classmethod
    def build(cls, ingredient: Ingredient, quantity_base: Decimal) -> Self:
        return cls(
            id=ingredient.id,
            name=ingredient.name,
            dimension=ingredient.dimension,
            default_unit=ingredient.default_unit,
            category=ingredient.category,
            grams_per_ml=ingredient.grams_per_ml,
            grams_per_piece=ingredient.grams_per_piece,
            stock=StockLevel.of(ingredient.dimension, quantity_base),
            created_at=ingredient.created_at,
            updated_at=ingredient.updated_at,
        )
