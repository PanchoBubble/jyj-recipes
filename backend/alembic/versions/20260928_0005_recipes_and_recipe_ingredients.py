"""recipes and recipe ingredients

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28 13:07:13.729109+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("photo_path", sa.String(length=255), nullable=True),
        sa.Column("default_servings", sa.Integer(), server_default=sa.text("2"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_recipes_name_not_blank")),
        sa.CheckConstraint(
            "default_servings > 0", name=op.f("ck_recipes_default_servings_positive")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_recipes_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipes")),
    )
    op.create_table(
        "recipe_ingredients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("ingredient_id", sa.Integer(), nullable=False),
        sa.Column("amount_per_person", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("unit_code", sa.String(length=16), nullable=False),
        sa.Column(
            "unit_dimension",
            postgresql.ENUM(name="unit_dimension", create_type=False),
            nullable=False,
        ),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "CASE WHEN unit_dimension = 'none' THEN coalesce(amount_per_person, 0) >= 0 "
            "ELSE coalesce(amount_per_person, 0) > 0 END",
            name=op.f("ck_recipe_ingredients_amount_positive_unless_dimensionless"),
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_recipe_ingredients_position_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name=op.f("fk_recipe_ingredients_ingredient_id_ingredients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["recipes.id"],
            name=op.f("fk_recipe_ingredients_recipe_id_recipes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unit_code", "unit_dimension"],
            ["units.code", "units.dimension"],
            name=op.f("fk_recipe_ingredients_unit_code_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipe_ingredients")),
        sa.UniqueConstraint(
            "recipe_id", "position", name=op.f("uq_recipe_ingredients_recipe_id_position")
        ),
    )
    op.create_index(
        "ix_recipe_ingredients_ingredient_id", "recipe_ingredients", ["ingredient_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_recipe_ingredients_ingredient_id", table_name="recipe_ingredients")
    op.drop_table("recipe_ingredients")
    op.drop_table("recipes")
