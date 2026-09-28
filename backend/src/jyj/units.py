"""Pure unit conversion. Decimal only, no floats, no database access.

Rounding rules:

- Every returned amount is quantized to ``QUANTUM`` (3 decimal places, matching the
  ``numeric(12,3)`` columns) with ``ROUNDING`` (half-even, so summing many converted lines
  does not drift upwards).
- Intermediate steps are computed at ``PRECISION`` significant digits and rounded once at
  the end, so a cross-dimension path (e.g. cup -> ml -> g -> piece) rounds only once.
- Because every seeded ``to_base`` factor is an integer, ``from_base(to_base(x, u), u) == x``
  for any ``x`` already on the quantum. The reverse is not guaranteed (1 ml is 0.004 cup,
  which is 0.96 ml).
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum

QUANTUM = Decimal("0.001")
ROUNDING = ROUND_HALF_EVEN
PRECISION = 34


class Dimension(StrEnum):
    MASS = "mass"
    VOLUME = "volume"
    COUNT = "count"
    NONE = "none"


BASE_UNIT_CODES: dict[Dimension, str] = {
    Dimension.MASS: "g",
    Dimension.VOLUME: "ml",
    Dimension.COUNT: "piece",
}


@dataclass(frozen=True, slots=True)
class Unit:
    code: str
    dimension: Dimension
    to_base: Decimal | None

    def __post_init__(self) -> None:
        if (self.dimension is Dimension.NONE) != (self.to_base is None):
            raise ValueError("to_base must be set exactly when the dimension is not 'none'")
        if self.to_base is not None and self.to_base <= 0:
            raise ValueError("to_base must be positive")


UNITS: dict[str, Unit] = {
    u.code: u
    for u in (
        Unit("g", Dimension.MASS, Decimal(1)),
        Unit("kg", Dimension.MASS, Decimal(1000)),
        Unit("ml", Dimension.VOLUME, Decimal(1)),
        Unit("l", Dimension.VOLUME, Decimal(1000)),
        Unit("tsp", Dimension.VOLUME, Decimal(5)),
        Unit("tbsp", Dimension.VOLUME, Decimal(15)),
        Unit("cup", Dimension.VOLUME, Decimal(240)),
        Unit("piece", Dimension.COUNT, Decimal(1)),
        Unit("pinch", Dimension.NONE, None),
        Unit("to_taste", Dimension.NONE, None),
    )
}


class UnknownUnitError(KeyError):
    pass


class DimensionlessUnitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IngredientConversions:
    grams_per_ml: Decimal | None = None
    grams_per_piece: Decimal | None = None

    def __post_init__(self) -> None:
        for value in (self.grams_per_ml, self.grams_per_piece):
            if value is not None and (_check_amount(value) <= 0):
                raise ValueError("ingredient conversion factors must be positive")


NO_CONVERSIONS = IngredientConversions()


@dataclass(frozen=True, slots=True)
class Quantity:
    amount: Decimal
    unit: Unit


class UnconvertibleReason(StrEnum):
    DIMENSIONLESS = "dimensionless"
    MISSING_GRAMS_PER_ML = "missing_grams_per_ml"
    MISSING_GRAMS_PER_PIECE = "missing_grams_per_piece"


@dataclass(frozen=True, slots=True)
class Unconvertible:
    reason: UnconvertibleReason
    from_unit: Unit
    to_unit: Unit


ConversionResult = Quantity | Unconvertible


def get_unit(unit: Unit | str) -> Unit:
    if isinstance(unit, Unit):
        return unit
    try:
        return UNITS[unit]
    except KeyError:
        raise UnknownUnitError(unit) from None


def base_unit(dimension: Dimension) -> Unit:
    try:
        return UNITS[BASE_UNIT_CODES[dimension]]
    except KeyError:
        raise DimensionlessUnitError(f"dimension {dimension!s} has no base unit") from None


def quantize(amount: Decimal) -> Decimal:
    return _check_amount(amount).quantize(QUANTUM, rounding=ROUNDING)


def to_base(amount: Decimal, unit: Unit | str) -> Decimal:
    """Amount expressed in the base unit (g, ml or piece) of the unit's dimension."""
    u = get_unit(unit)
    with localcontext(prec=PRECISION):
        return quantize(_check_amount(amount) * _factor(u))


def from_base(amount_base: Decimal, unit: Unit | str) -> Decimal:
    """Inverse of ``to_base``: a base-unit amount expressed in ``unit``."""
    u = get_unit(unit)
    with localcontext(prec=PRECISION):
        return quantize(_check_amount(amount_base) / _factor(u))


def convert(
    amount: Decimal,
    from_unit: Unit | str,
    to_unit: Unit | str,
    conversions: IngredientConversions = NO_CONVERSIONS,
) -> ConversionResult:
    """Convert between any two units, crossing dimensions only via the ingredient's factors.

    Never guesses: a missing factor or a dimensionless unit yields ``Unconvertible``.
    """
    src, dst = get_unit(from_unit), get_unit(to_unit)
    value = _check_amount(amount)

    if src.dimension is Dimension.NONE or dst.dimension is Dimension.NONE:
        return Unconvertible(UnconvertibleReason.DIMENSIONLESS, src, dst)

    with localcontext(prec=PRECISION):
        base = value * _factor(src)
        crossed = _cross(base, src.dimension, dst.dimension, conversions)
        if isinstance(crossed, UnconvertibleReason):
            return Unconvertible(crossed, src, dst)
        return Quantity(quantize(crossed / _factor(dst)), dst)


def display_quantity(amount_base: Decimal, dimension: Dimension) -> Quantity:
    """Pick a friendly metric unit for a base amount without losing precision.

    1500 g -> 1.5 kg and 2500 ml -> 2.5 l, but 1234.5 g stays in g because 1.2345 kg does not
    fit the quantum. Kitchen units (tsp, cup) are never chosen.
    """
    base = base_unit(dimension)
    value = quantize(amount_base)
    large = {Dimension.MASS: UNITS["kg"], Dimension.VOLUME: UNITS["l"]}.get(dimension)
    if large is not None and abs(value) >= _factor(large):
        with localcontext(prec=PRECISION):
            exact = value / _factor(large)
        if exact == quantize(exact):
            return Quantity(quantize(exact), large)
    return Quantity(value, base)


def _factor(unit: Unit) -> Decimal:
    if unit.to_base is None:
        raise DimensionlessUnitError(f"unit {unit.code!r} has no base conversion")
    return unit.to_base


def _cross(
    base: Decimal, src: Dimension, dst: Dimension, conversions: IngredientConversions
) -> Decimal | UnconvertibleReason:
    if src is dst:
        return base

    grams = _to_grams(base, src, conversions)
    if isinstance(grams, UnconvertibleReason):
        return grams
    if dst is Dimension.MASS:
        return grams
    if dst is Dimension.VOLUME:
        if conversions.grams_per_ml is None:
            return UnconvertibleReason.MISSING_GRAMS_PER_ML
        return grams / conversions.grams_per_ml
    if conversions.grams_per_piece is None:
        return UnconvertibleReason.MISSING_GRAMS_PER_PIECE
    return grams / conversions.grams_per_piece


def _to_grams(
    base: Decimal, dimension: Dimension, conversions: IngredientConversions
) -> Decimal | UnconvertibleReason:
    if dimension is Dimension.MASS:
        return base
    if dimension is Dimension.VOLUME:
        if conversions.grams_per_ml is None:
            return UnconvertibleReason.MISSING_GRAMS_PER_ML
        return base * conversions.grams_per_ml
    if conversions.grams_per_piece is None:
        return UnconvertibleReason.MISSING_GRAMS_PER_PIECE
    return base * conversions.grams_per_piece


def _check_amount(amount: Decimal) -> Decimal:
    if isinstance(amount, bool) or not isinstance(amount, Decimal | int):
        raise TypeError(f"amounts must be Decimal or int, got {type(amount).__name__}")
    value = Decimal(amount)
    if not value.is_finite():
        raise ValueError("amounts must be finite")
    return value
