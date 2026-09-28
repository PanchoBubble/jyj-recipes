"""meal slots and planned meals

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28 16:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_SLOTS = ("Lunch", "Dinner", "Tea")


def upgrade() -> None:
    slots = op.create_table(
        "meal_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_meal_slots_name_not_blank")),
        sa.CheckConstraint("position >= 0", name=op.f("ck_meal_slots_position_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meal_slots")),
    )
    op.create_index("uq_meal_slots_name_lower", "meal_slots", [sa.text("lower(name)")], unique=True)
    op.bulk_insert(slots, [{"name": n, "position": i} for i, n in enumerate(SEED_SLOTS)])

    status = postgresql.ENUM("planned", "cooked", "skipped", name="planned_meal_status")
    op.create_table(
        "planned_meals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("servings", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", status, server_default="planned", nullable=False),
        sa.Column("cooked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooked_by", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("servings > 0", name=op.f("ck_planned_meals_servings_positive")),
        sa.CheckConstraint("position >= 0", name=op.f("ck_planned_meals_position_non_negative")),
        sa.CheckConstraint(
            "(status = 'cooked') = (cooked_at IS NOT NULL)",
            name=op.f("ck_planned_meals_cooked_at_iff_cooked"),
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"],
            ["meal_slots.id"],
            name=op.f("fk_planned_meals_slot_id_meal_slots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["recipes.id"],
            name=op.f("fk_planned_meals_recipe_id_recipes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cooked_by"],
            ["users.id"],
            name=op.f("fk_planned_meals_cooked_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_planned_meals_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planned_meals")),
    )
    op.create_index(
        "ix_planned_meals_date_slot_id", "planned_meals", ["date", "slot_id"], unique=False
    )
    op.create_index("ix_planned_meals_recipe_id", "planned_meals", ["recipe_id"], unique=False)

    op.create_foreign_key(
        op.f("fk_stock_movements_planned_meal_id_planned_meals"),
        "stock_movements",
        "planned_meals",
        ["planned_meal_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_stock_movements_planned_meal_id", "stock_movements", ["planned_meal_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_stock_movements_planned_meal_id", table_name="stock_movements")
    op.drop_constraint(
        op.f("fk_stock_movements_planned_meal_id_planned_meals"),
        "stock_movements",
        type_="foreignkey",
    )
    op.drop_index("ix_planned_meals_recipe_id", table_name="planned_meals")
    op.drop_index("ix_planned_meals_date_slot_id", table_name="planned_meals")
    op.drop_table("planned_meals")
    postgresql.ENUM(name="planned_meal_status").drop(op.get_bind())
    op.drop_index("uq_meal_slots_name_lower", table_name="meal_slots")
    op.drop_table("meal_slots")
