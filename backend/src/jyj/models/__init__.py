"""ORM models.

Import every model module here so Alembic autogenerate sees its tables.
"""

from jyj.models.chat import (
    ChatAction,
    ChatActionStatus,
    ChatConversation,
    ChatInput,
    ChatMessage,
    ChatRole,
)
from jyj.models.ingredient import Ingredient
from jyj.models.meal_plan import MealSlot, PlannedMeal, PlannedMealStatus
from jyj.models.recipe import Recipe, RecipeIngredient
from jyj.models.shopping import (
    ShoppingItemKind,
    ShoppingList,
    ShoppingListItem,
    ShoppingListStatus,
)
from jyj.models.stock import StockItem, StockMovement, StockReason, StockSource
from jyj.models.unit import Unit
from jyj.models.user import AuthSession, User

__all__ = [
    "AuthSession",
    "ChatAction",
    "ChatActionStatus",
    "ChatConversation",
    "ChatInput",
    "ChatMessage",
    "ChatRole",
    "Ingredient",
    "MealSlot",
    "PlannedMeal",
    "PlannedMealStatus",
    "Recipe",
    "RecipeIngredient",
    "ShoppingItemKind",
    "ShoppingList",
    "ShoppingListItem",
    "ShoppingListStatus",
    "StockItem",
    "StockMovement",
    "StockReason",
    "StockSource",
    "Unit",
    "User",
]
