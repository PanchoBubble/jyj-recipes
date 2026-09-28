"""stock items and movements

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28 12:58:58.753076+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_items",
        sa.Column("ingredient_id", sa.Integer(), nullable=False),
        sa.Column(
            "quantity_base", sa.Numeric(precision=12, scale=3), server_default="0", nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("quantity_base >= 0", name=op.f("ck_stock_items_quantity_non_negative")),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name=op.f("fk_stock_items_ingredient_id_ingredients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ingredient_id", name=op.f("pk_stock_items")),
    )
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ingredient_id", sa.Integer(), nullable=False),
        sa.Column("delta_base", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column(
            "reason",
            sa.Enum("manual", "cooked", "purchased", "correction", "undo", name="stock_reason"),
            nullable=False,
        ),
        sa.Column(
            "shortfall_base", sa.Numeric(precision=12, scale=3), server_default="0", nullable=False
        ),
        sa.Column("planned_meal_id", sa.Integer(), nullable=True),
        sa.Column("shopping_list_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Enum("ui", "chat", name="stock_source"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "shortfall_base >= 0", name=op.f("ck_stock_movements_shortfall_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name=op.f("fk_stock_movements_ingredient_id_ingredients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_stock_movements_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_movements")),
    )
    op.create_index(
        "ix_stock_movements_ingredient_created",
        "stock_movements",
        ["ingredient_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_stock_movements_created_at", "stock_movements", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_stock_movements_created_at", table_name="stock_movements")
    op.drop_index("ix_stock_movements_ingredient_created", table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_table("stock_items")
    sa.Enum(name="stock_source").drop(op.get_bind())
    sa.Enum(name="stock_reason").drop(op.get_bind())
