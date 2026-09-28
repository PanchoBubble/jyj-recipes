from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jyj.db import Base
from jyj.models.ingredient import Ingredient


class StockReason(StrEnum):
    MANUAL = "manual"
    COOKED = "cooked"
    PURCHASED = "purchased"
    CORRECTION = "correction"
    UNDO = "undo"


class StockSource(StrEnum):
    UI = "ui"
    CHAT = "chat"


def _values(members: type[StrEnum]) -> list[str]:
    return [m.value for m in members]


stock_reason_enum = Enum(StockReason, name="stock_reason", values_callable=_values)
stock_source_enum = Enum(StockSource, name="stock_source", values_callable=_values)


class StockItem(Base):
    """Materialised balance of the stock_movements ledger, in the ingredient's base unit."""

    __tablename__ = "stock_items"
    __table_args__ = (CheckConstraint("quantity_base >= 0", name="quantity_non_negative"),)

    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True
    )
    quantity_base: Mapped[Decimal] = mapped_column(Numeric(12, 3), server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ingredient: Mapped[Ingredient] = relationship(lazy="joined", viewonly=True)


class StockMovement(Base):
    """Append-only ledger row. delta_base is what was applied, so the ledger sums to the balance."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("shortfall_base >= 0", name="shortfall_non_negative"),
        Index("ix_stock_movements_ingredient_created", "ingredient_id", "created_at"),
        Index("ix_stock_movements_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id", ondelete="RESTRICT"))
    delta_base: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    reason: Mapped[StockReason] = mapped_column(stock_reason_enum)
    shortfall_base: Mapped[Decimal] = mapped_column(Numeric(12, 3), server_default="0")
    # Plain ints until planned_meals / shopping_lists exist; FKs get added with those tables.
    planned_meal_id: Mapped[int | None]
    shopping_list_id: Mapped[int | None]
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    source: Mapped[StockSource] = mapped_column(stock_source_enum)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
