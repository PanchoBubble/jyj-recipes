"""Recipes with per-person ingredient lines. Shared by the REST API and chat tools.

Lines store ``amount_per_person`` in the unit the user picked; totals for N servings are
computed on read (``scale_recipe``) and never stored.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jyj.models import Ingredient, Recipe, RecipeIngredient, StockSource, User
from jyj.services import ingredients as ingredients_service
from jyj.services.errors import InvalidError, NotFoundError, ServiceError
from jyj.services.ingredients import _escape_like
from jyj.services.stock import MAX_QUANTITY
from jyj.units import (
    UNITS,
    Dimension,
    Quantity,
    Unconvertible,
    UnknownUnitError,
    base_unit,
    convert,
    display_quantity,
    get_unit,
    quantize,
    to_base,
)

NAME_MAX = 200
DESCRIPTION_MAX = 5000
NOTE_MAX = 200
SERVINGS_MAX = 100
LINES_MAX = 100
UPDATABLE_FIELDS = frozenset({"name", "description", "default_servings", "ingredients", "archived"})
REQUIRED_FIELDS = frozenset({"name", "default_servings", "ingredients", "archived"})

ReferenceCheck = Callable[[Session, int], bool]

# Register a check per table that points at recipes (e.g. planned meals); a referenced recipe
# is archived on delete instead of being removed.
REFERENCE_CHECKS: dict[str, ReferenceCheck] = {}


def find_references(db: Session, recipe_id: int) -> list[str]:
    return [name for name, check in REFERENCE_CHECKS.items() if check(db, recipe_id)]


class DeleteOutcome(StrEnum):
    DELETED = "deleted"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class RecipePage:
    items: list[Recipe]
    total: int


@dataclass(frozen=True, slots=True)
class ScaledLine:
    line: RecipeIngredient
    amount: Decimal | None
    display: Quantity | None


def list_recipes(
    db: Session,
    q: str | None = None,
    *,
    include_archived: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> RecipePage:
    stmt = select(Recipe)
    if q and q.strip():
        stmt = stmt.where(Recipe.name.ilike(f"%{_escape_like(q.strip())}%", escape="\\"))
    if not include_archived:
        stmt = stmt.where(Recipe.archived_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(func.lower(Recipe.name), Recipe.id).limit(limit).offset(offset)
    ).all()
    return RecipePage(list(items), total)


def get_recipe(db: Session, recipe_id: int) -> Recipe:
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise NotFoundError(f"recipe {recipe_id} not found")
    return recipe


def create_recipe(
    db: Session,
    user: User,
    source: StockSource,
    *,
    name: str,
    description: str | None = None,
    default_servings: int = 2,
    ingredients: Sequence[Mapping[str, Any]] = (),
) -> Recipe:
    recipe = Recipe(
        name=_clean_name(name),
        description=_clean_description(description),
        default_servings=_clean_servings(default_servings),
        created_by=user.id,
    )
    db.add(recipe)
    recipe.ingredients = _build_lines(db, user, source, ingredients)
    db.flush()
    db.refresh(recipe)
    return recipe


def update_recipe(
    db: Session, user: User, source: StockSource, recipe_id: int, changes: Mapping[str, Any]
) -> Recipe:
    """Partial update; ``ingredients``, when present, replaces the whole list in order."""
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise InvalidError(f"unknown fields: {', '.join(sorted(unknown))}")
    nulled = sorted(f for f in REQUIRED_FIELDS & set(changes) if changes[f] is None)
    if nulled:
        raise InvalidError(f"fields cannot be null: {', '.join(nulled)}")

    recipe = get_recipe(db, recipe_id)
    if "name" in changes:
        recipe.name = _clean_name(changes["name"])
    if "description" in changes:
        recipe.description = _clean_description(changes["description"])
    if "default_servings" in changes:
        recipe.default_servings = _clean_servings(changes["default_servings"])
    if "archived" in changes:
        if not changes["archived"]:
            recipe.archived_at = None
        elif recipe.archived_at is None:
            recipe.archived_at = datetime.now(UTC)
    if "ingredients" in changes:
        lines = _build_lines(db, user, source, changes["ingredients"])
        recipe.ingredients.clear()
        # Flush the removals first so the new rows can reuse (recipe_id, position).
        db.flush()
        recipe.ingredients.extend(lines)
        recipe.updated_at = func.now()

    db.flush()
    db.refresh(recipe)
    return recipe


def delete_recipe(db: Session, user: User, source: StockSource, recipe_id: int) -> DeleteOutcome:
    recipe = get_recipe(db, recipe_id)
    if find_references(db, recipe.id):
        if recipe.archived_at is None:
            recipe.archived_at = datetime.now(UTC)
        db.flush()
        return DeleteOutcome.ARCHIVED
    db.delete(recipe)
    db.flush()
    return DeleteOutcome.DELETED


def scale_recipe(recipe: Recipe, servings: int) -> list[ScaledLine]:
    servings = _clean_servings(servings)
    scaled = []
    for line in recipe.ingredients:
        if line.unit_dimension is Dimension.NONE or line.amount_per_person is None:
            scaled.append(ScaledLine(line, None, None))
            continue
        amount = quantize(line.amount_per_person * servings)
        shown = display_quantity(to_base(amount, line.unit_code), line.unit_dimension)
        scaled.append(ScaledLine(line, amount, shown))
    return scaled


def _build_lines(
    db: Session, user: User, source: StockSource, rows: Sequence[Mapping[str, Any]]
) -> list[RecipeIngredient]:
    if len(rows) > LINES_MAX:
        raise InvalidError(f"a recipe can have at most {LINES_MAX} ingredients")
    return [_build_line(db, user, source, index, row) for index, row in enumerate(rows)]


def _build_line(
    db: Session, user: User, source: StockSource, index: int, row: Mapping[str, Any]
) -> RecipeIngredient:
    where = f"ingredients[{index}]"

    def invalid(detail: str, **extensions: Any) -> InvalidError:
        return InvalidError(f"{where}: {detail}", row=index, **extensions)

    ingredient_id, new = row.get("ingredient_id"), row.get("new_ingredient")
    if (ingredient_id is None) == (new is None):
        raise invalid("provide exactly one of ingredient_id or new_ingredient")

    try:
        unit = get_unit(row.get("unit") or "")
    except UnknownUnitError:
        raise invalid(f"unknown unit {row.get('unit')!r}", known_units=sorted(UNITS)) from None
    amount = _clean_amount(row.get("amount_per_person"), unit.dimension, invalid)
    note = _clean_note(row.get("note"), invalid)

    try:
        if new is not None:
            ingredient = ingredients_service.create_ingredient(db, user, source, **new)
        else:
            ingredient = ingredients_service.get_ingredient(db, ingredient_id)
    except ServiceError as exc:
        raise type(exc)(f"{where}: {exc.detail}", row=index, **exc.extensions) from None

    _check_convertible(ingredient, unit.code, invalid)
    return RecipeIngredient(
        ingredient=ingredient,
        amount_per_person=amount,
        unit_code=unit.code,
        unit_dimension=unit.dimension,
        note=note,
        position=index,
    )


def _check_convertible(
    ingredient: Ingredient, unit_code: str, invalid: Callable[..., InvalidError]
) -> None:
    unit = get_unit(unit_code)
    if unit.dimension is Dimension.NONE:
        return
    result = convert(Decimal(1), unit, base_unit(ingredient.dimension), ingredient.conversions)
    if isinstance(result, Unconvertible):
        raise invalid(
            f"cannot use {unit.code!r} for {ingredient.name!r} ({ingredient.dimension.value})",
            reason=result.reason.value,
            unit=unit.code,
            base_unit=result.to_unit.code,
        )


def _clean_amount(
    value: Decimal | None, dimension: Dimension, invalid: Callable[..., InvalidError]
) -> Decimal | None:
    if value is None:
        if dimension is Dimension.NONE:
            return None
        raise invalid("amount_per_person is required for measurable units")
    if not isinstance(value, Decimal) or not value.is_finite():
        raise invalid("amount_per_person must be a finite number")
    amount = quantize(value)
    if amount > MAX_QUANTITY:
        raise invalid("amount_per_person is too large")
    if dimension is Dimension.NONE:
        if amount < 0:
            raise invalid("amount_per_person must not be negative")
        return amount
    if amount <= 0:
        raise invalid("amount_per_person must be greater than 0")
    return amount


def _clean_note(note: str | None, invalid: Callable[..., InvalidError]) -> str | None:
    if note is None:
        return None
    cleaned = " ".join(note.split())
    if len(cleaned) > NOTE_MAX:
        raise invalid(f"note must be at most {NOTE_MAX} characters")
    return cleaned or None


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise InvalidError("name must not be blank")
    if len(cleaned) > NAME_MAX:
        raise InvalidError(f"name must be at most {NAME_MAX} characters")
    return cleaned


def _clean_description(description: str | None) -> str | None:
    if description is None:
        return None
    cleaned = description.strip()
    if len(cleaned) > DESCRIPTION_MAX:
        raise InvalidError(f"description must be at most {DESCRIPTION_MAX} characters")
    return cleaned or None


def _clean_servings(servings: int) -> int:
    if isinstance(servings, bool) or not isinstance(servings, int):
        raise InvalidError("servings must be an integer")
    if not 1 <= servings <= SERVINGS_MAX:
        raise InvalidError(f"servings must be between 1 and {SERVINGS_MAX}")
    return servings
