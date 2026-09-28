"""Argument types and compact formatting shared by the chat tools."""

import datetime as dt
import re
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema, model_validator

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

DATE_RANGE_MAX_DAYS = 31
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso_date(value: object) -> dt.date:
    # Only YYYY-MM-DD strings: no timestamps, datetimes or relative words.
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise ValueError("use an ISO date like 2026-01-31")
    return dt.date.fromisoformat(value)


IsoDate = Annotated[
    dt.date,
    BeforeValidator(_iso_date),
    WithJsonSchema({"type": "string", "description": "ISO date, YYYY-MM-DD."}),
]


class DateRange(BaseModel):
    """Inclusive ``from``..``to`` range of at most ``DATE_RANGE_MAX_DAYS`` days."""

    model_config = ConfigDict(extra="forbid")

    start: IsoDate = Field(alias="from", description="First day (ISO date), inclusive.")
    end: IsoDate = Field(alias="to", description="Last day (ISO date), inclusive.")

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if self.end < self.start:
            raise ValueError("'to' must not be before 'from'")
        if (self.end - self.start).days + 1 > DATE_RANGE_MAX_DAYS:
            raise ValueError(f"the range can span at most {DATE_RANGE_MAX_DAYS} days")
        return self


def number(value: Decimal) -> str:
    """Decimal as a plain string without trailing zeros (1500.000 -> "1500")."""
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def quantity(amount_base: Decimal, dimension: Dimension) -> str:
    shown = display_quantity(amount_base, dimension)
    return f"{number(shown.amount)} {shown.unit.code}"
