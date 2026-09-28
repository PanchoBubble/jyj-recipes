"""shopping lists and items

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shopping_lists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("open", "done", name="shopping_list_status"),
            server_default="open",
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("start_date <= end_date", name=op.f("ck_shopping_lists_range_ordered")),
        sa.CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)",
            name=op.f("ck_shopping_lists_completed_at_iff_done"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_shopping_lists_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shopping_lists")),
    )
    op.create_index("ix_shopping_lists_created_at", "shopping_lists", ["created_at"], unique=False)

    op.create_table(
        "shopping_list_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("list_id", sa.Integer(), nullable=False),
        sa.Column("ingredient_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("buy", "unconverted", "check_have", name="shopping_item_kind"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("required_base", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("available_base", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("to_buy_base", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("display_unit", sa.String(length=16), nullable=False),
        sa.Column("checked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("bought_base", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column(
            "unconverted",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "recipe_names",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "required_base >= 0", name=op.f("ck_shopping_list_items_required_non_negative")
        ),
        sa.CheckConstraint(
            "available_base >= 0", name=op.f("ck_shopping_list_items_available_non_negative")
        ),
        sa.CheckConstraint(
            "to_buy_base >= 0", name=op.f("ck_shopping_list_items_to_buy_non_negative")
        ),
        sa.CheckConstraint(
            "bought_base >= 0", name=op.f("ck_shopping_list_items_bought_non_negative")
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_shopping_list_items_position_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["shopping_lists.id"],
            name=op.f("fk_shopping_list_items_list_id_shopping_lists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ingredient_id"],
            ["ingredients.id"],
            name=op.f("fk_shopping_list_items_ingredient_id_ingredients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["display_unit"],
            ["units.code"],
            name=op.f("fk_shopping_list_items_display_unit_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shopping_list_items")),
    )
    op.create_index(
        "ix_shopping_list_items_list_id", "shopping_list_items", ["list_id"], unique=False
    )
    op.create_index(
        "ix_shopping_list_items_ingredient_id",
        "shopping_list_items",
        ["ingredient_id"],
        unique=False,
    )

    # Rows written before this table existed may carry ids that never pointed anywhere.
    op.execute("UPDATE stock_movements SET shopping_list_id = NULL")
    op.create_foreign_key(
        op.f("fk_stock_movements_shopping_list_id_shopping_lists"),
        "stock_movements",
        "shopping_lists",
        ["shopping_list_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_stock_movements_shopping_list_id",
        "stock_movements",
        ["shopping_list_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_stock_movements_shopping_list_id", table_name="stock_movements")
    op.drop_constraint(
        op.f("fk_stock_movements_shopping_list_id_shopping_lists"),
        "stock_movements",
        type_="foreignkey",
    )
    op.drop_index("ix_shopping_list_items_ingredient_id", table_name="shopping_list_items")
    op.drop_index("ix_shopping_list_items_list_id", table_name="shopping_list_items")
    op.drop_table("shopping_list_items")
    postgresql.ENUM(name="shopping_item_kind").drop(op.get_bind())
    op.drop_index("ix_shopping_lists_created_at", table_name="shopping_lists")
    op.drop_table("shopping_lists")
    postgresql.ENUM(name="shopping_list_status").drop(op.get_bind())
