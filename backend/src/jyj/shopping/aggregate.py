"""Pure shopping aggregation over plain dataclasses. No ORM, no database, Decimal only.

For a date range ``[start, end]``:

- Every ``planned`` meal in the range needs ``amount_per_person x servings`` of each recipe
  line, converted to the ingredient's base unit (g, ml or piece) with ``jyj.units``.
- Stock is spoken for first by ``planned`` meals dated ``today .. start - 1`` (nothing is
  reserved when ``start <= today``): ``available = max(0, stock - reserved)`` and
  ``to_buy = max(0, required - available)``. No pack sizes or rounding up.
- Lines that cannot reach the ingredient's base unit are listed per ingredient as
  ``unconverted`` (summed per unit), never dropped or guessed.
- Dimensionless lines ("to taste", "pinch") carry no quantity; they become ``check_have``.
- Cooked and skipped meals are ignored everywhere.

Items are sorted by category (uncategorised last), then name. Items with nothing to buy are
still returned, flagged, so the caller decides whether to hide them.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from jyj.units import (
    Dimension,
    IngredientConversions,
    Quantity,
    Unconvertible,
    UnconvertibleReason,
    base_unit,
    convert,
    display_quantity,
    get_unit,
    quantize,
)

PLANNED = "planned"
ZERO = Decimal("0.000")


@dataclass(frozen=True, slots=True)
class MealInput:
    id: int
    date: dt.date
    slot_id: int | None
    slot_name: str | None
    # Order within the day.
    position: int
    recipe_id: int
    recipe_name: str
    servings: int
    status: str


@dataclass(frozen=True, slots=True)
class RecipeLineInput:
    recipe_id: int
    ingredient_id: int
    amount_per_person: Decimal | None
    unit_code: str
    position: int = 0


@dataclass(frozen=True, slots=True)
class IngredientInput:
    id: int
    name: str
    category: str | None
    dimension: Dimension
    conversions: IngredientConversions = field(default_factory=IngredientConversions)


@dataclass(frozen=True, slots=True)
class MealRef:
    planned_meal_id: int
    date: dt.date
    slot_id: int | None
    slot_name: str | None
    recipe_id: int
    recipe_name: str
    servings: int


@dataclass(frozen=True, slots=True)
class Contribution:
    meal: MealRef
    amount: Decimal
    unit_code: str
    amount_base: Decimal


@dataclass(frozen=True, slots=True)
class UnconvertedLine:
    amount: Decimal
    unit_code: str
    reason: UnconvertibleReason
    recipe_names: tuple[str, ...]
    meals: tuple[MealRef, ...]


@dataclass(frozen=True, slots=True)
class ShoppingItem:
    ingredient_id: int
    name: str
    category: str | None
    dimension: Dimension
    base_unit: str
    required_base: Decimal
    stock_base: Decimal
    reserved_base: Decimal
    available_base: Decimal
    to_buy_base: Decimal
    display: Quantity
    contributions: tuple[Contribution, ...]
    unconverted: tuple[UnconvertedLine, ...]

    @property
    def nothing_to_buy(self) -> bool:
        return self.to_buy_base == 0 and not self.unconverted


@dataclass(frozen=True, slots=True)
class CheckHaveItem:
    ingredient_id: int
    name: str
    category: str | None
    recipe_names: tuple[str, ...]
    meals: tuple[MealRef, ...]


@dataclass(frozen=True, slots=True)
class ShoppingPlan:
    start: dt.date
    end: dt.date
    today: dt.date
    items: tuple[ShoppingItem, ...]
    check_have: tuple[CheckHaveItem, ...]


def reservation_window(today: dt.date, start: dt.date) -> tuple[dt.date, dt.date] | None:
    """Inclusive dates whose planned meals reserve stock, or None when ``start <= today``."""
    if start <= today:
        return None
    return today, start - dt.timedelta(days=1)


def compute_shopping(
    meals: Iterable[MealInput],
    recipe_lines: Iterable[RecipeLineInput],
    ingredients: Iterable[IngredientInput],
    stock: Mapping[int, Decimal],
    today: dt.date,
    start: dt.date,
    end: dt.date,
) -> ShoppingPlan:
    if start > end:
        raise ValueError("start must be on or before end")

    by_id = {i.id: i for i in ingredients}
    lines_by_recipe: dict[int, list[RecipeLineInput]] = defaultdict(list)
    for line in sorted(recipe_lines, key=lambda r: (r.recipe_id, r.position)):
        lines_by_recipe[line.recipe_id].append(line)

    window = reservation_window(today, start)
    in_range: list[MealInput] = []
    reserving: list[MealInput] = []
    for meal in meals:
        if meal.status != PLANNED:
            continue
        if start <= meal.date <= end:
            in_range.append(meal)
        elif window is not None and window[0] <= meal.date <= window[1]:
            reserving.append(meal)

    reserved: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for meal in reserving:
        for line in lines_by_recipe.get(meal.recipe_id, ()):
            ingredient = _ingredient(by_id, line.ingredient_id)
            result = _line_base(line, meal, ingredient)
            if isinstance(result, Decimal):
                reserved[ingredient.id] += result

    contributions: dict[int, list[Contribution]] = defaultdict(list)
    unconverted: dict[int, dict[str, list[tuple[MealRef, Decimal, UnconvertibleReason]]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    to_taste: dict[int, list[MealRef]] = defaultdict(list)

    for meal in sorted(in_range, key=_meal_order):
        ref = _ref(meal)
        for line in lines_by_recipe.get(meal.recipe_id, ()):
            ingredient = _ingredient(by_id, line.ingredient_id)
            result = _line_base(line, meal, ingredient)
            if result is None:
                to_taste[ingredient.id].append(ref)
            elif isinstance(result, Unconvertible):
                amount = _scaled(line, meal)
                unconverted[ingredient.id][line.unit_code].append((ref, amount, result.reason))
            else:
                contributions[ingredient.id].append(
                    Contribution(ref, _scaled(line, meal), line.unit_code, result)
                )

    items = [
        _item(
            by_id[ingredient_id],
            contributions.get(ingredient_id, []),
            unconverted.get(ingredient_id, {}),
            stock.get(ingredient_id, ZERO),
            reserved.get(ingredient_id, ZERO),
        )
        for ingredient_id in contributions.keys() | unconverted.keys()
    ]
    check_have = [
        CheckHaveItem(
            ingredient_id=ingredient_id,
            name=by_id[ingredient_id].name,
            category=by_id[ingredient_id].category,
            recipe_names=_distinct(r.recipe_name for r in refs),
            meals=tuple(refs),
        )
        for ingredient_id, refs in to_taste.items()
    ]
    return ShoppingPlan(
        start=start,
        end=end,
        today=today,
        items=tuple(sorted(items, key=lambda i: _sort_key(i.category, i.name, i.ingredient_id))),
        check_have=tuple(
            sorted(check_have, key=lambda i: _sort_key(i.category, i.name, i.ingredient_id))
        ),
    )


def _item(
    ingredient: IngredientInput,
    contributions: list[Contribution],
    unconverted: Mapping[str, list[tuple[MealRef, Decimal, UnconvertibleReason]]],
    stock_base: Decimal,
    reserved_base: Decimal,
) -> ShoppingItem:
    required = quantize(sum((c.amount_base for c in contributions), ZERO))
    stock_base = quantize(stock_base)
    reserved_base = quantize(reserved_base)
    available = max(ZERO, stock_base - reserved_base)
    to_buy = max(ZERO, required - available)
    return ShoppingItem(
        ingredient_id=ingredient.id,
        name=ingredient.name,
        category=ingredient.category,
        dimension=ingredient.dimension,
        base_unit=base_unit(ingredient.dimension).code,
        required_base=required,
        stock_base=stock_base,
        reserved_base=reserved_base,
        available_base=available,
        to_buy_base=to_buy,
        display=display_quantity(to_buy, ingredient.dimension),
        contributions=tuple(contributions),
        unconverted=tuple(
            UnconvertedLine(
                amount=quantize(sum((amount for _, amount, _ in entries), ZERO)),
                unit_code=unit_code,
                reason=entries[0][2],
                recipe_names=_distinct(ref.recipe_name for ref, _, _ in entries),
                meals=tuple(ref for ref, _, _ in entries),
            )
            for unit_code, entries in sorted(unconverted.items())
        ),
    )


def _line_base(
    line: RecipeLineInput, meal: MealInput, ingredient: IngredientInput
) -> Decimal | Unconvertible | None:
    """Base-unit amount for one meal's line, ``Unconvertible``, or None for "to taste"."""
    unit = get_unit(line.unit_code)
    if unit.dimension is Dimension.NONE:
        return None
    result = convert(
        _scaled(line, meal), unit, base_unit(ingredient.dimension), ingredient.conversions
    )
    if isinstance(result, Unconvertible):
        return result
    return result.amount


def _scaled(line: RecipeLineInput, meal: MealInput) -> Decimal:
    if line.amount_per_person is None:
        raise ValueError(f"measured line for recipe {line.recipe_id} has no amount_per_person")
    return line.amount_per_person * meal.servings


def _ingredient(by_id: Mapping[int, IngredientInput], ingredient_id: int) -> IngredientInput:
    try:
        return by_id[ingredient_id]
    except KeyError:
        raise ValueError(f"recipe line references unknown ingredient {ingredient_id}") from None


def _ref(meal: MealInput) -> MealRef:
    return MealRef(
        planned_meal_id=meal.id,
        date=meal.date,
        slot_id=meal.slot_id,
        slot_name=meal.slot_name,
        recipe_id=meal.recipe_id,
        recipe_name=meal.recipe_name,
        servings=meal.servings,
    )


def _meal_order(meal: MealInput) -> tuple[dt.date, int, int]:
    return meal.date, meal.position, meal.id


def _sort_key(category: str | None, name: str, ingredient_id: int) -> tuple:
    return (category is None, (category or "").casefold(), name.casefold(), ingredient_id)


def _distinct(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(names))
