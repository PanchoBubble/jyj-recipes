"""ORM models.

Import every model module here so Alembic autogenerate sees its tables.
"""

from jyj.models.ingredient import Ingredient
from jyj.models.unit import Unit

__all__ = ["Ingredient", "Unit"]
