import datetime as dt
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jyj.db import Base, TimestampMixin
from jyj.models.recipe import Recipe


class PlannedMealStatus(StrEnum):
    PLANNED = "planned"
    COOKED = "cooked"
    SKIPPED = "skipped"


planned_meal_status_enum = Enum(
    PlannedMealStatus,
    name="planned_meal_status",
    values_callable=lambda members: [m.value for m in members],
)


class MealSlot(Base):
    """A column of the meal calendar (lunch, dinner, ...), ordered by ``position``."""

    __tablename__ = "meal_slots"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    position: Mapped[int]
    active: Mapped[bool] = mapped_column(server_default=text("true"))


Index("uq_meal_slots_name_lower", func.lower(MealSlot.name), unique=True)


class PlannedMeal(TimestampMixin, Base):
    """A recipe placed in a (date, slot) cell; ``position`` orders meals within the cell."""

    __tablename__ = "planned_meals"
    __table_args__ = (
        CheckConstraint("servings > 0", name="servings_positive"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(
            "(status = 'cooked') = (cooked_at IS NOT NULL)", name="cooked_at_iff_cooked"
        ),
        Index("ix_planned_meals_date_slot_id", "date", "slot_id"),
        Index("ix_planned_meals_recipe_id", "recipe_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date)
    slot_id: Mapped[int] = mapped_column(ForeignKey("meal_slots.id", ondelete="RESTRICT"))
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="RESTRICT"))
    servings: Mapped[int]
    position: Mapped[int] = mapped_column(server_default=text("0"))
    status: Mapped[PlannedMealStatus] = mapped_column(
        planned_meal_status_enum, server_default=PlannedMealStatus.PLANNED.value
    )
    cooked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cooked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    slot: Mapped[MealSlot] = relationship(lazy="joined", innerjoin=True)
    recipe: Mapped[Recipe] = relationship(lazy="joined", innerjoin=True)
