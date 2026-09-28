"""Data checks for the migration that makes the planned meal slot optional."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

BEFORE = "0011"
AFTER = "0012"


@pytest.fixture
def at_before(db_engine: Engine, alembic_config: Config) -> Iterator[Engine]:
    command.downgrade(alembic_config, BEFORE)
    try:
        yield db_engine
    finally:
        # Dropping everything also wipes the rows these tests committed.
        command.downgrade(alembic_config, "base")
        command.upgrade(alembic_config, "head")


def run(engine: Engine, sql: str, **params) -> list[tuple]:
    with engine.begin() as connection:
        result = connection.execute(text(sql), params)
        return [tuple(row) for row in result] if result.returns_rows else []


def slot_ids(engine: Engine) -> dict[str, int]:
    return dict(run(engine, "SELECT name, id FROM meal_slots"))


def meals(engine: Engine) -> dict[str, tuple]:
    rows = run(
        engine,
        """
        SELECT r.name, m.date::text, s.name, m.position
        FROM planned_meals m
        JOIN recipes r ON r.id = m.recipe_id
        LEFT JOIN meal_slots s ON s.id = m.slot_id
        """,
    )
    return {name: (date, slot, position) for name, date, slot, position in rows}


def seed(engine: Engine, cells: list[tuple[str, str, str, int]]) -> None:
    (user_id,) = run(
        engine,
        "INSERT INTO users (username, display_name, password_hash) "
        "VALUES ('cook', 'Cook', 'x') RETURNING id",
    )[0]
    slots = slot_ids(engine)
    for recipe, date, slot, position in cells:
        run(
            engine,
            """
            WITH r AS (
                INSERT INTO recipes (name, created_by) VALUES (:recipe, :user) RETURNING id
            )
            INSERT INTO planned_meals (date, slot_id, recipe_id, servings, position, created_by)
            SELECT CAST(:date AS date), :slot, r.id, 2, :position, :user FROM r
            """,
            recipe=recipe,
            date=date,
            slot=slots[slot],
            position=position,
            user=user_id,
        )


def test_upgrade_orders_each_day_by_slot_then_position_and_downgrade_restores_cells(
    at_before: Engine, alembic_config: Config
) -> None:
    engine = at_before
    run(
        engine,
        "UPDATE meal_slots SET position = "
        "CASE name WHEN 'Tea' THEN 0 WHEN 'Lunch' THEN 1 ELSE 2 END",
    )
    seed(
        engine,
        [
            ("dinner-mon", "2026-10-05", "Dinner", 0),
            ("lunch-mon-a", "2026-10-05", "Lunch", 0),
            ("lunch-mon-b", "2026-10-05", "Lunch", 1),
            ("tea-mon", "2026-10-05", "Tea", 0),
            ("dinner-tue", "2026-10-06", "Dinner", 0),
        ],
    )

    command.upgrade(alembic_config, AFTER)

    assert meals(engine) == {
        "tea-mon": ("2026-10-05", "Tea", 0),
        "lunch-mon-a": ("2026-10-05", "Lunch", 1),
        "lunch-mon-b": ("2026-10-05", "Lunch", 2),
        "dinner-mon": ("2026-10-05", "Dinner", 3),
        "dinner-tue": ("2026-10-06", "Dinner", 0),
    }

    run(engine, "DELETE FROM meal_slots WHERE name = 'Tea'")
    assert meals(engine)["tea-mon"] == ("2026-10-05", None, 0)
    run(engine, "UPDATE meal_slots SET active = false WHERE name = 'Lunch'")

    command.downgrade(alembic_config, BEFORE)

    # The label-less meal joins the first active slot and keeps its place before the others.
    assert meals(engine) == {
        "tea-mon": ("2026-10-05", "Dinner", 0),
        "dinner-mon": ("2026-10-05", "Dinner", 1),
        "lunch-mon-a": ("2026-10-05", "Lunch", 0),
        "lunch-mon-b": ("2026-10-05", "Lunch", 1),
        "dinner-tue": ("2026-10-06", "Dinner", 0),
    }
    nullable = run(
        engine,
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'planned_meals' AND column_name = 'slot_id'",
    )
    assert nullable == [("NO",)]


def test_downgrade_without_any_slot_creates_one(at_before: Engine, alembic_config: Config) -> None:
    engine = at_before
    seed(engine, [("soup", "2026-10-05", "Lunch", 0), ("stew", "2026-10-05", "Dinner", 0)])
    command.upgrade(alembic_config, AFTER)
    run(engine, "DELETE FROM meal_slots")

    command.downgrade(alembic_config, BEFORE)

    assert list(slot_ids(engine)) == ["Meal"]
    assert meals(engine) == {
        "soup": ("2026-10-05", "Meal", 0),
        "stew": ("2026-10-05", "Meal", 1),
    }
