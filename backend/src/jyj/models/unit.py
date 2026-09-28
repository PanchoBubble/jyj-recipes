from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from jyj.db import Base
from jyj.units import Dimension

dimension_enum = Enum(
    Dimension,
    name="unit_dimension",
    values_callable=lambda members: [m.value for m in members],
)


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        # Target of the ingredients composite FK that pins default_unit to the dimension.
        UniqueConstraint("code", "dimension"),
        CheckConstraint("(dimension = 'none') = (to_base IS NULL)", name="to_base_iff_dimension"),
        CheckConstraint("to_base > 0", name="to_base_positive"),
    )

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    dimension: Mapped[Dimension] = mapped_column(dimension_enum)
    to_base: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
