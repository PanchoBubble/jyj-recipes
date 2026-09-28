from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jyj.db import Base, TimestampMixin
from jyj.models.ingredient import Ingredient
from jyj.models.unit import dimension_enum
from jyj.units import Dimension


class Recipe(TimestampMixin, Base):
    __tablename__ = "recipes"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("default_servings > 0", name="default_servings_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    photo_path: Mapped[str | None] = mapped_column(String(255))
    # {provider, photographer, photographer_url, page_url} for photos from a photo search.
    photo_credit: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    default_servings: Mapped[int] = mapped_column(server_default=text("2"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe",
        order_by="RecipeIngredient.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class RecipeIngredient(Base):
    """One per-person ingredient line; totals are amount_per_person x servings."""

    __tablename__ = "recipe_ingredients"
    __table_args__ = (
        # unit_dimension is denormalised so the CHECK below can see it; the composite FK keeps
        # it in sync with units.
        ForeignKeyConstraint(
            ["unit_code", "unit_dimension"],
            ["units.code", "units.dimension"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "CASE WHEN unit_dimension = 'none' THEN coalesce(amount_per_person, 0) >= 0 "
            "ELSE coalesce(amount_per_person, 0) > 0 END",
            name="amount_positive_unless_dimensionless",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
        UniqueConstraint("recipe_id", "position"),
        Index("ix_recipe_ingredients_ingredient_id", "ingredient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id", ondelete="RESTRICT"))
    amount_per_person: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    unit_code: Mapped[str] = mapped_column(String(16))
    unit_dimension: Mapped[Dimension] = mapped_column(dimension_enum)
    note: Mapped[str | None] = mapped_column(String(200))
    position: Mapped[int]

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")
    ingredient: Mapped[Ingredient] = relationship(lazy="joined")
