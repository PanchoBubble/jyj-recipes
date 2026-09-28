from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jyj.db import Base, TimestampMixin
from jyj.models.unit import Unit, dimension_enum
from jyj.units import Dimension, IngredientConversions


class Ingredient(TimestampMixin, Base):
    __tablename__ = "ingredients"
    __table_args__ = (
        ForeignKeyConstraint(
            ["default_unit", "dimension"],
            ["units.code", "units.dimension"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("dimension <> 'none'", name="dimension_measurable"),
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("grams_per_ml > 0", name="grams_per_ml_positive"),
        CheckConstraint("grams_per_piece > 0", name="grams_per_piece_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    dimension: Mapped[Dimension] = mapped_column(dimension_enum)
    default_unit: Mapped[str] = mapped_column(String(16))
    category: Mapped[str | None] = mapped_column(String(50))
    grams_per_ml: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    grams_per_piece: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))

    unit: Mapped[Unit] = relationship(viewonly=True)

    @property
    def conversions(self) -> IngredientConversions:
        return IngredientConversions(self.grams_per_ml, self.grams_per_piece)


Index("uq_ingredients_name_lower", func.lower(Ingredient.name), unique=True)
