"""units and ingredients

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 14:00:00.000000+00:00
"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIMENSIONS = ("mass", "volume", "count", "none")

# Frozen copy of jyj.units.UNITS at the time of this revision; tests assert they still match.
SEED_UNITS = (
    ("g", "mass", Decimal(1)),
    ("kg", "mass", Decimal(1000)),
    ("ml", "volume", Decimal(1)),
    ("l", "volume", Decimal(1000)),
    ("tsp", "volume", Decimal(5)),
    ("tbsp", "volume", Decimal(15)),
    ("cup", "volume", Decimal(240)),
    ("piece", "count", Decimal(1)),
    ("pinch", "none", None),
    ("to_taste", "none", None),
)


def upgrade() -> None:
    dimension = postgresql.ENUM(*DIMENSIONS, name="unit_dimension", create_type=False)
    dimension.create(op.get_bind())

    units = op.create_table(
        "units",
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("dimension", dimension, nullable=False),
        sa.Column("to_base", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.CheckConstraint(
            "(dimension = 'none') = (to_base IS NULL)", name=op.f("ck_units_to_base_iff_dimension")
        ),
        sa.CheckConstraint("to_base > 0", name=op.f("ck_units_to_base_positive")),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_units")),
        sa.UniqueConstraint("code", "dimension", name=op.f("uq_units_code_dimension")),
    )
    op.bulk_insert(
        units,
        [{"code": c, "dimension": d, "to_base": t} for c, d, t in SEED_UNITS],
    )

    op.create_table(
        "ingredients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("dimension", dimension, nullable=False),
        sa.Column("default_unit", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("grams_per_ml", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("grams_per_piece", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_ingredients_name_not_blank")),
        sa.CheckConstraint("dimension <> 'none'", name=op.f("ck_ingredients_dimension_measurable")),
        sa.CheckConstraint("grams_per_ml > 0", name=op.f("ck_ingredients_grams_per_ml_positive")),
        sa.CheckConstraint(
            "grams_per_piece > 0", name=op.f("ck_ingredients_grams_per_piece_positive")
        ),
        sa.ForeignKeyConstraint(
            ["default_unit", "dimension"],
            ["units.code", "units.dimension"],
            name=op.f("fk_ingredients_default_unit_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingredients")),
    )
    op.create_index(
        "uq_ingredients_name_lower", "ingredients", [sa.text("lower(name)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_ingredients_name_lower", table_name="ingredients")
    op.drop_table("ingredients")
    op.drop_table("units")
    postgresql.ENUM(name="unit_dimension").drop(op.get_bind())
