"""Ingredient catalogue. Shared by the REST API and chat tools."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jyj.models import Ingredient, StockItem, StockMovement, StockSource, User
from jyj.services.errors import ConflictError, InvalidError, NotFoundError
from jyj.units import UNITS, Dimension, UnknownUnitError, base_unit, get_unit

NAME_MAX = 100
CATEGORY_MAX = 50
UPDATABLE_FIELDS = frozenset(
    {"name", "dimension", "default_unit", "category", "grams_per_ml", "grams_per_piece"}
)
REQUIRED_FIELDS = frozenset({"name", "dimension", "default_unit"})

ReferenceCheck = Callable[[Session, int], bool]


@dataclass(frozen=True, slots=True)
class IngredientWithStock:
    ingredient: Ingredient
    quantity_base: Decimal


def _has_stock_movements(db: Session, ingredient_id: int) -> bool:
    return bool(db.scalar(select(exists().where(StockMovement.ingredient_id == ingredient_id))))


# Register a check per table that points at ingredients (e.g. recipe lines) so deletes and
# dimension changes are refused while anything still depends on the ingredient.
REFERENCE_CHECKS: dict[str, ReferenceCheck] = {
    "stock_movements": _has_stock_movements,
}


def find_references(db: Session, ingredient_id: int) -> list[str]:
    return [name for name, check in REFERENCE_CHECKS.items() if check(db, ingredient_id)]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_ingredients(db: Session, q: str | None = None) -> list[IngredientWithStock]:
    stmt = (
        select(Ingredient, func.coalesce(StockItem.quantity_base, 0))
        .outerjoin(StockItem, StockItem.ingredient_id == Ingredient.id)
        .order_by(func.lower(Ingredient.name), Ingredient.id)
    )
    if q and q.strip():
        stmt = stmt.where(Ingredient.name.ilike(f"%{_escape_like(q.strip())}%", escape="\\"))
    return [IngredientWithStock(i, Decimal(qty)) for i, qty in db.execute(stmt)]


def get_ingredient(db: Session, ingredient_id: int) -> Ingredient:
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise NotFoundError(f"ingredient {ingredient_id} not found")
    return ingredient


def stock_of(db: Session, ingredient_id: int) -> Decimal:
    qty = db.scalar(select(StockItem.quantity_base).where(StockItem.ingredient_id == ingredient_id))
    return qty if qty is not None else Decimal(0)


def create_ingredient(
    db: Session,
    user: User,
    source: StockSource,
    *,
    name: str,
    dimension: Dimension | str,
    default_unit: str | None = None,
    category: str | None = None,
    grams_per_ml: Decimal | None = None,
    grams_per_piece: Decimal | None = None,
) -> Ingredient:
    dim = _clean_dimension(dimension)
    ingredient = Ingredient(
        name=_clean_name(name),
        dimension=dim,
        default_unit=_clean_default_unit(default_unit or base_unit(dim).code, dim),
        category=_clean_category(category),
        grams_per_ml=_clean_factor("grams_per_ml", grams_per_ml),
        grams_per_piece=_clean_factor("grams_per_piece", grams_per_piece),
    )
    _ensure_name_free(db, ingredient.name)
    db.add(ingredient)
    _flush_unique(db, ingredient.name)
    return ingredient


def update_ingredient(
    db: Session, user: User, source: StockSource, ingredient_id: int, changes: Mapping[str, Any]
) -> Ingredient:
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise InvalidError(f"unknown fields: {', '.join(sorted(unknown))}")
    nulled = sorted(f for f in REQUIRED_FIELDS & set(changes) if changes[f] is None)
    if nulled:
        raise InvalidError(f"fields cannot be null: {', '.join(nulled)}")

    ingredient = get_ingredient(db, ingredient_id)

    if "name" in changes:
        name = _clean_name(changes["name"])
        if name.lower() != ingredient.name.lower():
            _ensure_name_free(db, name)
        ingredient.name = name

    dim = ingredient.dimension
    if "dimension" in changes:
        dim = _clean_dimension(changes["dimension"])
        if dim is not ingredient.dimension:
            refs = find_references(db, ingredient.id)
            if refs:
                raise ConflictError(
                    "cannot change the dimension of an ingredient that is in use",
                    references=refs,
                )
    if "default_unit" in changes:
        unit = _clean_default_unit(changes["default_unit"], dim)
    elif dim is not ingredient.dimension:
        unit = base_unit(dim).code
    else:
        unit = ingredient.default_unit
    ingredient.dimension = dim
    ingredient.default_unit = unit

    if "category" in changes:
        ingredient.category = _clean_category(changes["category"])
    for factor in ("grams_per_ml", "grams_per_piece"):
        if factor in changes:
            setattr(ingredient, factor, _clean_factor(factor, changes[factor]))

    _flush_unique(db, ingredient.name)
    db.refresh(ingredient)
    return ingredient


def delete_ingredient(db: Session, user: User, source: StockSource, ingredient_id: int) -> None:
    ingredient = get_ingredient(db, ingredient_id)
    refs = find_references(db, ingredient.id)
    if refs:
        raise ConflictError("ingredient is in use and cannot be deleted", references=refs)
    db.delete(ingredient)
    db.flush()


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise InvalidError("name must not be blank")
    if len(cleaned) > NAME_MAX:
        raise InvalidError(f"name must be at most {NAME_MAX} characters")
    return cleaned


def _clean_category(category: str | None) -> str | None:
    if category is None:
        return None
    cleaned = " ".join(category.split())
    if len(cleaned) > CATEGORY_MAX:
        raise InvalidError(f"category must be at most {CATEGORY_MAX} characters")
    return cleaned or None


def _clean_dimension(dimension: Dimension | str) -> Dimension:
    try:
        dim = Dimension(dimension)
    except ValueError:
        raise InvalidError(f"unknown dimension {dimension!r}") from None
    if dim is Dimension.NONE:
        raise InvalidError("ingredients must have a measurable dimension (mass, volume or count)")
    return dim


def _clean_default_unit(code: str, dimension: Dimension) -> str:
    try:
        unit = get_unit(code)
    except UnknownUnitError:
        raise InvalidError(f"unknown unit {code!r}", known_units=sorted(UNITS)) from None
    if unit.dimension is not dimension:
        raise InvalidError(
            f"default unit {code!r} is {unit.dimension.value}, ingredient is {dimension.value}"
        )
    return unit.code


def _clean_factor(field: str, value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if not value.is_finite() or value <= 0:
        raise InvalidError(f"{field} must be a positive number")
    return value


def _ensure_name_free(db: Session, name: str) -> None:
    taken = db.scalar(select(Ingredient.id).where(func.lower(Ingredient.name) == name.lower()))
    if taken is not None:
        raise ConflictError(f"an ingredient named {name!r} already exists", existing_id=taken)


def _flush_unique(db: Session, name: str) -> None:
    # The pre-check races with concurrent creates; the unique index is the real guard.
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        if "uq_ingredients_name_lower" in str(exc.orig):
            raise ConflictError(f"an ingredient named {name!r} already exists") from None
        raise
