"""planned meals: optional slot, positions per day

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-28 23:00:00.000000+00:00

Each day becomes one ordered list of meals; the slot is an optional label. Upgrade renumbers
positions per day in (slot position, old position, id) order. Downgrade gives label-less meals
the first active slot (any slot if none is active, a new "Meal" slot if there are none) and
renumbers positions per (date, slot) cell, keeping their relative day order.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_planned_meals_slot_id_meal_slots"


def _replace_slot_fk(ondelete: str) -> None:
    op.drop_constraint(FK_NAME, "planned_meals", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME, "planned_meals", "meal_slots", ["slot_id"], ["id"], ondelete=ondelete
    )


def upgrade() -> None:
    _replace_slot_fk("SET NULL")
    op.alter_column("planned_meals", "slot_id", existing_type=sa.Integer(), nullable=True)
    op.execute(
        """
        UPDATE planned_meals AS pm SET position = ranked.day_position
        FROM (
            SELECT m.id, row_number() OVER (
                PARTITION BY m.date ORDER BY s.position, s.id, m.position, m.id
            ) - 1 AS day_position
            FROM planned_meals AS m LEFT JOIN meal_slots AS s ON s.id = m.slot_id
        ) AS ranked
        WHERE pm.id = ranked.id AND pm.position <> ranked.day_position
        """
    )
    op.drop_index("ix_planned_meals_date_slot_id", table_name="planned_meals")
    op.create_index(
        "ix_planned_meals_date_position", "planned_meals", ["date", "position"], unique=False
    )
    op.create_index("ix_planned_meals_slot_id", "planned_meals", ["slot_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM planned_meals WHERE slot_id IS NULL LIMIT 1")).first():
        fallback = bind.execute(
            sa.text("SELECT id FROM meal_slots ORDER BY active DESC, position, id LIMIT 1")
        ).scalar()
        if fallback is None:
            fallback = bind.execute(
                sa.text("INSERT INTO meal_slots (name, position) VALUES ('Meal', 0) RETURNING id")
            ).scalar_one()
        bind.execute(
            sa.text("UPDATE planned_meals SET slot_id = :slot WHERE slot_id IS NULL"),
            {"slot": fallback},
        )
    op.execute(
        """
        UPDATE planned_meals AS pm SET position = ranked.cell_position
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY date, slot_id ORDER BY position, id
            ) - 1 AS cell_position
            FROM planned_meals
        ) AS ranked
        WHERE pm.id = ranked.id AND pm.position <> ranked.cell_position
        """
    )
    op.drop_index("ix_planned_meals_slot_id", table_name="planned_meals")
    op.drop_index("ix_planned_meals_date_position", table_name="planned_meals")
    op.create_index(
        "ix_planned_meals_date_slot_id", "planned_meals", ["date", "slot_id"], unique=False
    )
    op.alter_column("planned_meals", "slot_id", existing_type=sa.Integer(), nullable=False)
    _replace_slot_fk("RESTRICT")
