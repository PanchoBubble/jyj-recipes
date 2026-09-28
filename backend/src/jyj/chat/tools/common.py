"""Argument types and compact formatting shared by the chat tools."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, WithJsonSchema

from jyj.units import Dimension, display_quantity

# The model sends JSON numbers; Pydantic turns them into exact Decimals via their repr.
Amount = Annotated[
    Decimal,
    Field(allow_inf_nan=False, max_digits=18, decimal_places=6),
    WithJsonSchema({"type": "number"}),
]
Id = Annotated[int, Field(ge=1)]
UnitCode = Annotated[
    str, Field(min_length=1, max_length=16, description="Unit code, e.g. g, kg, ml, l, piece.")
]
Query = Annotated[str, Field(max_length=100)]
MeasurableDimension = Literal["mass", "volume", "count"]


def number(value: Decimal) -> str:
    """Decimal as a plain string without trailing zeros (1500.000 -> "1500")."""
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def quantity(amount_base: Decimal, dimension: Dimension) -> str:
    shown = display_quantity(amount_base, dimension)
    return f"{number(shown.amount)} {shown.unit.code}"
