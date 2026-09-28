import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jyj.db import Base
from jyj.models.ingredient import Ingredient


class ShoppingListStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class ShoppingItemKind(StrEnum):
    # Something to buy: to_buy_base > 0 (unconverted lines may ride along).
    BUY = "buy"
    # Nothing measurable to buy, but some lines could not be converted to the base unit.
    UNCONVERTED = "unconverted"
    # Only "to taste" lines: a "have it?" reminder with no quantity.
    CHECK_HAVE = "check_have"


def _values(members: type[StrEnum]) -> list[str]:
    return [m.value for m in members]


shopping_list_status_enum = Enum(
    ShoppingListStatus, name="shopping_list_status", values_callable=_values
)
shopping_item_kind_enum = Enum(ShoppingItemKind, name="shopping_item_kind", values_callable=_values)


class ShoppingList(Base):
    """A stored snapshot of the shopping plan for ``start_date .. end_date``."""

    __tablename__ = "shopping_lists"
    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="range_ordered"),
        CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)", name="completed_at_iff_done"
        ),
        Index("ix_shopping_lists_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[ShoppingListStatus] = mapped_column(
        shopping_list_status_enum, server_default=ShoppingListStatus.OPEN.value
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["ShoppingListItem"]] = relationship(
        back_populates="shopping_list",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="ShoppingListItem.position",
    )


class ShoppingListItem(Base):
    """One snapshot line. Quantities are in the base unit of ``display_unit``'s dimension."""

    __tablename__ = "shopping_list_items"
    __table_args__ = (
        CheckConstraint("required_base >= 0", name="required_non_negative"),
        CheckConstraint("available_base >= 0", name="available_non_negative"),
        CheckConstraint("to_buy_base >= 0", name="to_buy_non_negative"),
        CheckConstraint("bought_base >= 0", name="bought_non_negative"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        Index("ix_shopping_list_items_list_id", "list_id"),
        Index("ix_shopping_list_items_ingredient_id", "ingredient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("shopping_lists.id", ondelete="CASCADE"))
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id", ondelete="RESTRICT"))
    kind: Mapped[ShoppingItemKind] = mapped_column(shopping_item_kind_enum)
    category: Mapped[str | None] = mapped_column(String(50))
    required_base: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    available_base: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    to_buy_base: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    display_unit: Mapped[str] = mapped_column(
        String(16), ForeignKey("units.code", ondelete="RESTRICT")
    )
    checked: Mapped[bool] = mapped_column(server_default=text("false"))
    bought_base: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    unconverted: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    recipe_names: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    position: Mapped[int]

    shopping_list: Mapped[ShoppingList] = relationship(back_populates="items")
    ingredient: Mapped[Ingredient] = relationship(lazy="joined", innerjoin=True, viewonly=True)
