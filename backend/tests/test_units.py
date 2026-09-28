from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from jyj import units
from jyj.units import (
    UNITS,
    Dimension,
    DimensionlessUnitError,
    IngredientConversions,
    Quantity,
    Unconvertible,
    UnconvertibleReason,
    Unit,
    UnknownUnitError,
    convert,
    display_quantity,
    from_base,
    to_base,
)

D = Decimal
MEASURABLE = [u for u in UNITS.values() if u.dimension is not Dimension.NONE]
DIMENSIONLESS = [u for u in UNITS.values() if u.dimension is Dimension.NONE]
EGG = IngredientConversions(grams_per_piece=D("60"))
MILK = IngredientConversions(grams_per_ml=D("1.03"))
FLOUR = IngredientConversions(grams_per_ml=D("0.593"), grams_per_piece=D("1000"))

amounts = st.decimals(
    min_value=D("-999999.999"), max_value=D("999999.999"), places=3, allow_nan=False
)
measurable_units = st.sampled_from(MEASURABLE)


def test_seeded_units() -> None:
    assert {c: (u.dimension.value, u.to_base) for c, u in UNITS.items()} == {
        "g": ("mass", D(1)),
        "kg": ("mass", D(1000)),
        "ml": ("volume", D(1)),
        "l": ("volume", D(1000)),
        "tsp": ("volume", D(5)),
        "tbsp": ("volume", D(15)),
        "cup": ("volume", D(240)),
        "piece": ("count", D(1)),
        "pinch": ("none", None),
        "to_taste": ("none", None),
    }


@pytest.mark.parametrize("unit", MEASURABLE, ids=lambda u: u.code)
def test_every_measurable_unit_round_trips(unit: Unit) -> None:
    for amount in (D("0"), D("0.001"), D("1"), D("2.5"), D("123.456")):
        base = to_base(amount, unit)
        assert base == amount * unit.to_base
        assert from_base(base, unit) == amount
        assert convert(amount, unit, units.base_unit(unit.dimension)) == Quantity(
            base, units.base_unit(unit.dimension)
        )


@pytest.mark.parametrize(
    ("amount", "unit", "base"),
    [
        (D("1.5"), "kg", D("1500")),
        (D("2"), "l", D("2000")),
        (D("1"), "tsp", D("5")),
        (D("2"), "tbsp", D("30")),
        (D("0.5"), "cup", D("120")),
        (D("3"), "piece", D("3")),
    ],
)
def test_to_base_examples(amount: Decimal, unit: str, base: Decimal) -> None:
    assert to_base(amount, unit) == base


@pytest.mark.parametrize("unit", DIMENSIONLESS, ids=lambda u: u.code)
def test_dimensionless_units_have_no_base(unit: Unit) -> None:
    with pytest.raises(DimensionlessUnitError):
        to_base(D(1), unit)
    with pytest.raises(DimensionlessUnitError):
        from_base(D(1), unit)


@pytest.mark.parametrize("unit", DIMENSIONLESS, ids=lambda u: u.code)
@pytest.mark.parametrize("other", list(UNITS.values()), ids=lambda u: u.code)
def test_dimensionless_units_never_convert(unit: Unit, other: Unit) -> None:
    for src, dst in ((unit, other), (other, unit)):
        result = convert(D(1), src, dst, FLOUR)
        assert result == Unconvertible(UnconvertibleReason.DIMENSIONLESS, src, dst)


def test_within_dimension_conversion() -> None:
    assert convert(D("3"), "tsp", "tbsp") == Quantity(D("1.000"), UNITS["tbsp"])
    assert convert(D("1"), "cup", "tbsp") == Quantity(D("16.000"), UNITS["tbsp"])
    assert convert(D("250"), "g", "kg") == Quantity(D("0.250"), UNITS["kg"])


def test_mass_volume_both_ways() -> None:
    assert convert(D("1"), "l", "g", MILK) == Quantity(D("1030.000"), UNITS["g"])
    assert convert(D("1030"), "g", "l", MILK) == Quantity(D("1.000"), UNITS["l"])
    assert convert(D("1"), "cup", "g", FLOUR) == Quantity(D("142.320"), UNITS["g"])


def test_mass_count_both_ways() -> None:
    assert convert(D("2"), "piece", "g", EGG) == Quantity(D("120.000"), UNITS["g"])
    assert convert(D("0.3"), "kg", "piece", EGG) == Quantity(D("5.000"), UNITS["piece"])


def test_volume_count_goes_through_grams() -> None:
    assert convert(D("1"), "piece", "cup", FLOUR) == Quantity(D("7.026"), UNITS["cup"])
    assert convert(D("1"), "cup", "piece", FLOUR) == Quantity(D("0.142"), UNITS["piece"])


@pytest.mark.parametrize(
    ("src", "dst", "conversions", "reason"),
    [
        ("g", "ml", EGG, UnconvertibleReason.MISSING_GRAMS_PER_ML),
        ("cup", "kg", EGG, UnconvertibleReason.MISSING_GRAMS_PER_ML),
        ("piece", "g", MILK, UnconvertibleReason.MISSING_GRAMS_PER_PIECE),
        ("kg", "piece", MILK, UnconvertibleReason.MISSING_GRAMS_PER_PIECE),
        ("piece", "ml", EGG, UnconvertibleReason.MISSING_GRAMS_PER_ML),
        ("ml", "piece", MILK, UnconvertibleReason.MISSING_GRAMS_PER_PIECE),
        ("g", "ml", IngredientConversions(), UnconvertibleReason.MISSING_GRAMS_PER_ML),
    ],
)
def test_missing_conversion_is_unconvertible(
    src: str, dst: str, conversions: IngredientConversions, reason: UnconvertibleReason
) -> None:
    assert convert(D(1), src, dst, conversions) == Unconvertible(reason, UNITS[src], UNITS[dst])


def test_cross_dimension_without_conversions_never_guesses() -> None:
    assert isinstance(convert(D(1), "ml", "g"), Unconvertible)


def test_rounding_is_half_even_on_three_places() -> None:
    assert from_base(D("0.0005"), "g") == D("0.000")
    assert from_base(D("0.0015"), "g") == D("0.002")
    assert from_base(D("1"), "cup") == D("0.004")
    assert to_base(D("0.004"), "cup") == D("0.960")
    assert to_base(D("1.0005"), "g") == D("1.000")


def test_cross_dimension_rounds_once_at_the_end() -> None:
    # 1 ml is 0.0015 g; rounding that hop first would give 0.002 g, i.e. 2 pieces.
    tiny = IngredientConversions(grams_per_ml=D("0.0015"), grams_per_piece=D("0.001"))
    assert convert(D("1"), "ml", "piece", tiny) == Quantity(D("1.500"), UNITS["piece"])


@pytest.mark.parametrize(
    ("amount", "dimension", "expected"),
    [
        (D("1500"), Dimension.MASS, Quantity(D("1.500"), UNITS["kg"])),
        (D("1000"), Dimension.MASS, Quantity(D("1.000"), UNITS["kg"])),
        (D("999.999"), Dimension.MASS, Quantity(D("999.999"), UNITS["g"])),
        (D("250"), Dimension.VOLUME, Quantity(D("250.000"), UNITS["ml"])),
        (D("2500"), Dimension.VOLUME, Quantity(D("2.500"), UNITS["l"])),
        (D("-1500"), Dimension.MASS, Quantity(D("-1.500"), UNITS["kg"])),
        (D("1234.5"), Dimension.MASS, Quantity(D("1234.500"), UNITS["g"])),
        (D("12"), Dimension.COUNT, Quantity(D("12.000"), UNITS["piece"])),
    ],
)
def test_display_quantity(amount: Decimal, dimension: Dimension, expected: Quantity) -> None:
    assert display_quantity(amount, dimension) == expected


def test_display_quantity_rejects_dimensionless() -> None:
    with pytest.raises(DimensionlessUnitError):
        display_quantity(D(1), Dimension.NONE)


@pytest.mark.parametrize("bad", [1.5, "1.5", None, True])
def test_non_decimal_amounts_are_rejected(bad: object) -> None:
    with pytest.raises(TypeError):
        to_base(bad, "g")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        convert(bad, "g", "kg")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D("-Infinity")])
def test_non_finite_amounts_are_rejected(bad: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        to_base(bad, "g")


def test_ints_are_accepted() -> None:
    assert to_base(2, "kg") == D("2000")


def test_unknown_unit() -> None:
    with pytest.raises(UnknownUnitError):
        to_base(D(1), "stone")
    with pytest.raises(UnknownUnitError):
        convert(D(1), "g", "oz")


@pytest.mark.parametrize("bad", [D(0), D(-1)])
def test_conversion_factors_must_be_positive(bad: Decimal) -> None:
    with pytest.raises(ValueError, match="positive"):
        IngredientConversions(grams_per_ml=bad)
    with pytest.raises(ValueError, match="positive"):
        IngredientConversions(grams_per_piece=bad)


def test_unit_invariants() -> None:
    with pytest.raises(ValueError):
        Unit("x", Dimension.NONE, D(1))
    with pytest.raises(ValueError):
        Unit("x", Dimension.MASS, None)
    with pytest.raises(ValueError):
        Unit("x", Dimension.MASS, D(0))


@given(amount=amounts, unit=measurable_units)
def test_property_round_trip(amount: Decimal, unit: Unit) -> None:
    assert from_base(to_base(amount, unit), unit) == amount


@given(amount=amounts, src=measurable_units, dst=measurable_units)
def test_property_same_dimension_matches_base_math(amount: Decimal, src: Unit, dst: Unit) -> None:
    result = convert(amount, src, dst, FLOUR)
    assert isinstance(result, Quantity)
    assert result.unit == dst
    assert result.amount.as_tuple().exponent == -3
    if src.dimension is dst.dimension:
        assert result.amount == from_base(to_base(amount, src), dst)


@given(
    amount=amounts,
    src=measurable_units,
    dst=measurable_units,
    gpm=st.none() | st.decimals(min_value=D("0.001"), max_value=D("50"), places=3),
    gpp=st.none() | st.decimals(min_value=D("0.001"), max_value=D("5000"), places=3),
)
def test_property_never_guesses(
    amount: Decimal, src: Unit, dst: Unit, gpm: Decimal | None, gpp: Decimal | None
) -> None:
    result = convert(amount, src, dst, IngredientConversions(gpm, gpp))
    dims = {src.dimension, dst.dimension}
    needs_gpm = Dimension.VOLUME in dims and len(dims) == 2
    needs_gpp = Dimension.COUNT in dims and len(dims) == 2
    convertible = (not needs_gpm or gpm is not None) and (not needs_gpp or gpp is not None)
    assert isinstance(result, Quantity) is convertible


@given(amount=st.decimals(min_value=D("0"), max_value=D("99999"), places=3))
def test_property_mass_volume_inverse_within_rounding(amount: Decimal) -> None:
    there = convert(amount, "g", "ml", MILK)
    assert isinstance(there, Quantity)
    back = convert(there.amount, "ml", "g", MILK)
    assert isinstance(back, Quantity)
    # One quantum of ml error is at most grams_per_ml grams after converting back.
    assert abs(back.amount - amount) <= D("0.001") * MILK.grams_per_ml + D("0.0005")


@given(amount=amounts)
def test_property_display_quantity_preserves_amount(amount: Decimal) -> None:
    for dimension in (Dimension.MASS, Dimension.VOLUME, Dimension.COUNT):
        shown = display_quantity(amount, dimension)
        assert to_base(shown.amount, shown.unit) == amount
