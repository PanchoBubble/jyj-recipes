from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jyj.models import Ingredient, Unit
from jyj.units import UNITS, Dimension, IngredientConversions, Quantity, convert

D = Decimal


def make(**overrides: object) -> Ingredient:
    fields: dict[str, object] = {
        "name": "Flour",
        "dimension": Dimension.MASS,
        "default_unit": "g",
    }
    fields.update(overrides)
    return Ingredient(**fields)


def assert_rejected(session: Session, ingredient: Ingredient, constraint: str) -> None:
    session.add(ingredient)
    with pytest.raises(IntegrityError, match=constraint):
        session.flush()
    session.rollback()


def test_seeded_units_match_the_pure_module(db_session: Session) -> None:
    rows = db_session.scalars(select(Unit)).all()

    assert {u.code: (u.dimension, u.to_base) for u in rows} == {
        u.code: (u.dimension, u.to_base) for u in UNITS.values()
    }


def test_ingredient_round_trips_and_exposes_conversions(db_session: Session) -> None:
    egg = make(name="Egg", dimension=Dimension.COUNT, default_unit="piece", grams_per_piece=D(60))
    db_session.add(egg)
    db_session.flush()
    db_session.refresh(egg)

    assert egg.id is not None
    assert egg.created_at is not None
    assert egg.unit.code == "piece"
    assert egg.conversions == IngredientConversions(grams_per_piece=D("60.000"))
    assert convert(D(2), "piece", "g", egg.conversions) == Quantity(D("120.000"), UNITS["g"])


@pytest.mark.parametrize(("dimension", "unit"), [(Dimension.MASS, "ml"), (Dimension.COUNT, "kg")])
def test_default_unit_must_match_dimension(
    db_session: Session, dimension: Dimension, unit: str
) -> None:
    assert_rejected(
        db_session,
        make(dimension=dimension, default_unit=unit),
        "fk_ingredients_default_unit_units",
    )


def test_default_unit_must_exist(db_session: Session) -> None:
    assert_rejected(db_session, make(default_unit="oz"), "fk_ingredients_default_unit_units")


def test_ingredient_cannot_be_dimensionless(db_session: Session) -> None:
    assert_rejected(
        db_session,
        make(dimension=Dimension.NONE, default_unit="pinch"),
        "ck_ingredients_dimension_measurable",
    )


def test_names_are_unique_case_insensitively(db_session: Session) -> None:
    db_session.add(make(name="Flour"))
    db_session.flush()

    assert_rejected(db_session, make(name="fLOUR"), "uq_ingredients_name_lower")


def test_name_cannot_be_blank(db_session: Session) -> None:
    assert_rejected(db_session, make(name="   "), "ck_ingredients_name_not_blank")


@pytest.mark.parametrize("field", ["grams_per_ml", "grams_per_piece"])
@pytest.mark.parametrize("value", [D(0), D(-1)])
def test_conversion_factors_must_be_positive(
    db_session: Session, field: str, value: Decimal
) -> None:
    assert_rejected(db_session, make(**{field: value}), f"ck_ingredients_{field}_positive")


def test_units_in_use_cannot_be_deleted(db_session: Session) -> None:
    db_session.add(make())
    db_session.flush()

    with pytest.raises(IntegrityError, match="fk_ingredients_default_unit_units"):
        db_session.delete(db_session.get(Unit, "g"))
        db_session.flush()
