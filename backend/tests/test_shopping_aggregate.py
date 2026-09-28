import datetime as dt
import itertools
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from jyj.shopping.aggregate import (
    IngredientInput,
    MealInput,
    RecipeLineInput,
    ShoppingItem,
    ShoppingPlan,
    compute_shopping,
    reservation_window,
)
from jyj.units import UNITS, Dimension, IngredientConversions, Quantity, UnconvertibleReason

D = Decimal
TODAY = dt.date(2026, 10, 1)
START = dt.date(2026, 10, 5)
END = dt.date(2026, 10, 11)

FLOUR = IngredientInput(1, "Flour", "Baking", Dimension.MASS)
MILK = IngredientInput(2, "Milk", "Dairy", Dimension.VOLUME, IngredientConversions(D("1.03")))
EGG = IngredientInput(3, "Egg", "Dairy", Dimension.COUNT, IngredientConversions(None, D("60")))
SALT = IngredientInput(4, "Salt", None, Dimension.MASS)
BUTTER = IngredientInput(5, "butter", "Dairy", Dimension.MASS)
INGREDIENTS = [FLOUR, MILK, EGG, SALT, BUTTER]

PANCAKES = 10
CREPES = 11
BREAD = 12
NAMES = {PANCAKES: "Pancakes", CREPES: "Crepes", BREAD: "Bread"}
_ids = itertools.count(1000)


def meal(
    day: dt.date,
    recipe_id: int = PANCAKES,
    servings: int = 2,
    status: str = "planned",
    meal_id: int | None = None,
    slot: tuple[int, str, int] = (1, "Lunch", 0),
) -> MealInput:
    return MealInput(
        id=meal_id if meal_id is not None else next(_ids),
        date=day,
        slot_id=slot[0],
        slot_name=slot[1],
        slot_position=slot[2],
        recipe_id=recipe_id,
        recipe_name=NAMES[recipe_id],
        servings=servings,
        status=status,
    )


def line(recipe_id: int, ingredient: IngredientInput, amount: str | None, unit: str, pos: int = 0):
    return RecipeLineInput(
        recipe_id, ingredient.id, None if amount is None else D(amount), unit, pos
    )


def run(
    meals: list[MealInput],
    lines: list[RecipeLineInput],
    stock: dict[int, Decimal] | None = None,
    today: dt.date = TODAY,
    start: dt.date = START,
    end: dt.date = END,
) -> ShoppingPlan:
    return compute_shopping(meals, lines, INGREDIENTS, stock or {}, today, start, end)


def item(plan: ShoppingPlan, ingredient: IngredientInput) -> ShoppingItem:
    (found,) = [i for i in plan.items if i.ingredient_id == ingredient.id]
    return found


def totals(found: ShoppingItem) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    return found.required_base, found.reserved_base, found.available_base, found.to_buy_base


@pytest.mark.parametrize(
    ("lines", "ingredient", "stock", "expected"),
    [
        pytest.param(
            [line(PANCAKES, FLOUR, "0.1", "kg"), line(PANCAKES, FLOUR, "25", "g", 1)],
            FLOUR,
            {},
            (D("250"), D("0"), D("0"), D("250")),
            id="mixed-mass-units",
        ),
        pytest.param(
            [line(PANCAKES, MILK, "1", "cup"), line(PANCAKES, MILK, "2", "tbsp", 1)],
            MILK,
            {MILK.id: D("100")},
            (D("540"), D("0"), D("100"), D("440")),
            id="mixed-volume-units",
        ),
        pytest.param(
            [line(PANCAKES, MILK, "103", "g")],
            MILK,
            {},
            (D("200"), D("0"), D("0"), D("200")),
            id="cross-dimension-with-density",
        ),
        pytest.param(
            [line(PANCAKES, EGG, "90", "g")],
            EGG,
            {EGG.id: D("1")},
            (D("3"), D("0"), D("1"), D("2")),
            id="cross-dimension-with-piece-weight",
        ),
        pytest.param(
            [line(PANCAKES, FLOUR, "100", "g")],
            FLOUR,
            {FLOUR.id: D("0")},
            (D("200"), D("0"), D("0"), D("200")),
            id="zero-stock",
        ),
        pytest.param(
            [line(PANCAKES, FLOUR, "100", "g")],
            FLOUR,
            {FLOUR.id: D("5000")},
            (D("200"), D("0"), D("5000"), D("0")),
            id="stock-exceeds-need",
        ),
        pytest.param(
            [line(PANCAKES, FLOUR, "0.333", "g")],
            FLOUR,
            {FLOUR.id: D("0.1")},
            (D("0.666"), D("0"), D("0.1"), D("0.566")),
            id="decimal-precision",
        ),
    ],
)
def test_single_meal_cases(lines, ingredient, stock, expected) -> None:
    found = item(run([meal(START)], lines, stock), ingredient)

    assert totals(found) == expected
    assert all(isinstance(v, Decimal) for v in totals(found))
    assert not found.unconverted


def test_cross_dimension_without_conversion_goes_to_unconverted() -> None:
    lines = [
        line(PANCAKES, FLOUR, "100", "g"),
        line(PANCAKES, FLOUR, "1", "cup", 1),
        line(PANCAKES, FLOUR, "2", "piece", 2),
    ]
    plan = run([meal(START), meal(END, servings=1)], lines)

    found = item(plan, FLOUR)
    assert totals(found) == (D("300"), D("0"), D("0"), D("300"))
    assert [(u.unit_code, u.amount, u.reason) for u in found.unconverted] == [
        ("cup", D("3"), UnconvertibleReason.MISSING_GRAMS_PER_ML),
        ("piece", D("6"), UnconvertibleReason.MISSING_GRAMS_PER_PIECE),
    ]
    assert found.unconverted[0].recipe_names == ("Pancakes",)
    assert [m.date for m in found.unconverted[0].meals] == [START, END]
    assert not found.nothing_to_buy


def test_ingredient_with_only_unconverted_lines_is_still_listed() -> None:
    plan = run([meal(START)], [line(PANCAKES, FLOUR, "1", "cup")], {FLOUR.id: D("1000")})

    found = item(plan, FLOUR)
    assert totals(found) == (D("0"), D("0"), D("1000"), D("0"))
    assert found.unconverted[0].amount == D("2")
    assert not found.nothing_to_buy


def test_to_taste_lines_become_check_have_without_quantity() -> None:
    lines = [line(PANCAKES, SALT, None, "to_taste"), line(CREPES, SALT, "0", "pinch")]
    plan = run([meal(START), meal(START, recipe_id=CREPES), meal(END)], lines)

    assert plan.items == ()
    (check,) = plan.check_have
    assert (check.ingredient_id, check.name, check.category) == (SALT.id, "Salt", None)
    assert check.recipe_names == ("Pancakes", "Crepes")
    assert len(check.meals) == 3


def test_ingredient_can_be_both_measured_and_to_taste() -> None:
    lines = [line(PANCAKES, SALT, "2", "g"), line(CREPES, SALT, None, "to_taste")]
    plan = run([meal(START), meal(START, recipe_id=CREPES)], lines)

    assert item(plan, SALT).required_base == D("4")
    assert [c.recipe_names for c in plan.check_have] == [("Crepes",)]


def test_reservation_from_meals_before_the_range() -> None:
    meals = [
        meal(TODAY),
        meal(START - dt.timedelta(days=1), servings=1),
        meal(TODAY - dt.timedelta(days=1), servings=50),
        meal(START),
    ]
    plan = run(meals, [line(PANCAKES, FLOUR, "100", "g")], {FLOUR.id: D("250")})

    found = item(plan, FLOUR)
    assert totals(found) == (D("200"), D("300"), D("0"), D("200"))
    assert found.stock_base == D("250")
    assert [c.meal.date for c in found.contributions] == [START]


def test_reservation_leaves_the_remainder_available() -> None:
    meals = [meal(TODAY, servings=1), meal(START, servings=3)]
    plan = run(meals, [line(PANCAKES, FLOUR, "100", "g")], {FLOUR.id: D("250")})

    assert totals(item(plan, FLOUR)) == (D("300"), D("100"), D("150"), D("150"))


def test_reservation_ignores_cooked_skipped_and_unconvertible_lines() -> None:
    meals = [
        meal(TODAY, status="cooked"),
        meal(TODAY, status="skipped", slot=(2, "Dinner", 1)),
        meal(TODAY, recipe_id=CREPES),
        meal(START),
    ]
    lines = [line(PANCAKES, FLOUR, "100", "g"), line(CREPES, FLOUR, "1", "cup")]
    plan = run(meals, lines, {FLOUR.id: D("200")})

    assert totals(item(plan, FLOUR)) == (D("200"), D("0"), D("200"), D("0"))


@pytest.mark.parametrize(
    "start", [TODAY, TODAY - dt.timedelta(days=3)], ids=["starts-today", "starts-in-past"]
)
def test_no_reservation_when_range_starts_today_or_earlier(start: dt.date) -> None:
    meals = [meal(start), meal(TODAY + dt.timedelta(days=1))]
    plan = run(meals, [line(PANCAKES, FLOUR, "100", "g")], {FLOUR.id: D("300")}, start=start)

    assert totals(item(plan, FLOUR)) == (D("400"), D("0"), D("300"), D("100"))
    assert reservation_window(TODAY, start) is None


def test_reservation_window() -> None:
    assert reservation_window(TODAY, START) == (TODAY, dt.date(2026, 10, 4))
    assert reservation_window(TODAY, TODAY + dt.timedelta(days=1)) == (TODAY, TODAY)


def test_cooked_and_skipped_meals_in_range_are_excluded() -> None:
    meals = [meal(START, status="cooked"), meal(START, status="skipped"), meal(END, servings=1)]
    plan = run(meals, [line(PANCAKES, FLOUR, "100", "g")])

    found = item(plan, FLOUR)
    assert found.required_base == D("100")
    assert [c.meal.date for c in found.contributions] == [END]


def test_meals_outside_the_range_do_not_count() -> None:
    meals = [meal(END + dt.timedelta(days=1)), meal(START)]
    plan = run(meals, [line(PANCAKES, FLOUR, "100", "g")])

    assert item(plan, FLOUR).required_base == D("200")


@pytest.mark.parametrize("servings", [1, 2, 7])
def test_servings_scale_linearly(servings: int) -> None:
    plan = run([meal(START, servings=servings)], [line(PANCAKES, EGG, "1.5", "piece")])

    found = item(plan, EGG)
    assert found.required_base == D("1.5") * servings
    assert found.contributions[0].amount == D("1.5") * servings


def test_multiple_recipes_sharing_an_ingredient() -> None:
    meals = [
        meal(START, meal_id=1, slot=(2, "Dinner", 1)),
        meal(START, recipe_id=CREPES, servings=4, meal_id=2),
        meal(START + dt.timedelta(days=1), recipe_id=BREAD, servings=1, meal_id=3),
    ]
    lines = [
        line(PANCAKES, FLOUR, "80", "g"),
        line(CREPES, FLOUR, "0.05", "kg"),
        line(BREAD, FLOUR, "500", "g"),
        line(CREPES, MILK, "0.1", "l", 1),
    ]
    plan = run(meals, lines, {FLOUR.id: D("300")})

    found = item(plan, FLOUR)
    assert totals(found) == (D("860"), D("0"), D("300"), D("560"))
    assert [(c.meal.planned_meal_id, c.amount_base) for c in found.contributions] == [
        (2, D("200")),
        (1, D("160")),
        (3, D("500")),
    ]
    assert found.contributions[1].meal.slot_name == "Dinner"
    assert found.contributions[0].unit_code == "kg"
    assert found.contributions[0].amount == D("0.20")


def test_items_sorted_by_category_then_name_uncategorised_last() -> None:
    lines = [
        line(PANCAKES, SALT, "1", "g"),
        line(PANCAKES, MILK, "1", "ml", 1),
        line(PANCAKES, EGG, "1", "piece", 2),
        line(PANCAKES, FLOUR, "1", "g", 3),
        line(PANCAKES, BUTTER, "1", "g", 4),
    ]
    plan = run([meal(START)], lines)

    assert [i.name for i in plan.items] == ["Flour", "butter", "Egg", "Milk", "Salt"]


def test_zero_to_buy_items_are_reported_and_flagged() -> None:
    lines = [line(PANCAKES, FLOUR, "100", "g"), line(PANCAKES, EGG, "1", "piece", 1)]
    plan = run([meal(START)], lines, {FLOUR.id: D("200")})

    assert item(plan, FLOUR).nothing_to_buy
    assert not item(plan, EGG).nothing_to_buy


@pytest.mark.parametrize(
    ("amount", "unit", "display"),
    [
        ("750", "g", Quantity(D("1.5"), UNITS["kg"])),
        ("617.25", "g", Quantity(D("1234.5"), UNITS["g"])),
        ("1.25", "l", Quantity(D("2.5"), UNITS["l"])),
        ("3", "piece", Quantity(D("6"), UNITS["piece"])),
    ],
)
def test_display_uses_a_friendly_unit(amount: str, unit: str, display: Quantity) -> None:
    ingredient = {"g": FLOUR, "l": MILK, "piece": EGG}[unit]
    found = item(run([meal(START)], [line(PANCAKES, ingredient, amount, unit)]), ingredient)

    assert found.display == display
    assert found.base_unit == {"g": "g", "l": "ml", "piece": "piece"}[unit]


def test_empty_inputs_give_an_empty_plan() -> None:
    plan = run([], [])

    assert (plan.items, plan.check_have) == ((), ())
    assert (plan.start, plan.end, plan.today) == (START, END, TODAY)


def test_start_after_end_is_rejected() -> None:
    with pytest.raises(ValueError, match="start"):
        run([], [], start=END, end=START)


def test_unknown_ingredient_is_rejected() -> None:
    ghost = IngredientInput(99, "Ghost", None, Dimension.MASS)
    with pytest.raises(ValueError, match="unknown ingredient"):
        run([meal(START)], [line(PANCAKES, ghost, "1", "g")])


amounts = st.decimals(min_value=D("0.001"), max_value=D("5000"), places=3)
line_units = st.sampled_from(["g", "kg", "ml", "l", "tsp", "tbsp", "cup", "piece", "to_taste"])
day_offsets = st.integers(min_value=-3, max_value=14)


@st.composite
def scenarios(draw):
    ingredients = [
        IngredientInput(
            n,
            f"Ing {n}",
            draw(st.sampled_from([None, "A", "B"])),
            draw(st.sampled_from([Dimension.MASS, Dimension.VOLUME, Dimension.COUNT])),
            IngredientConversions(
                draw(st.none() | st.decimals(min_value=D("0.1"), max_value=D("5"), places=3)),
                draw(st.none() | st.decimals(min_value=D("1"), max_value=D("500"), places=3)),
            ),
        )
        for n in range(draw(st.integers(1, 4)))
    ]
    recipe_ids = list(NAMES)
    lines = [
        RecipeLineInput(
            draw(st.sampled_from(recipe_ids)),
            draw(st.sampled_from(ingredients)).id,
            draw(amounts),
            draw(line_units),
            pos,
        )
        for pos in range(draw(st.integers(0, 8)))
    ]
    meals = [
        meal(
            TODAY + dt.timedelta(days=draw(day_offsets)),
            recipe_id=draw(st.sampled_from(recipe_ids)),
            servings=draw(st.integers(1, 12)),
            status=draw(st.sampled_from(["planned", "planned", "cooked", "skipped"])),
            meal_id=n,
        )
        for n in range(draw(st.integers(0, 10)))
    ]
    stock = {
        i.id: draw(st.decimals(min_value=D("0"), max_value=D("10000"), places=3))
        for i in ingredients
        if draw(st.booleans())
    }
    start = TODAY + dt.timedelta(days=draw(day_offsets))
    end = start + dt.timedelta(days=draw(st.integers(0, 7)))
    return meals, lines, ingredients, stock, start, end


@given(scenarios())
def test_properties(scenario) -> None:
    meals, lines, ingredients, stock, start, end = scenario
    plan = compute_shopping(meals, lines, ingredients, stock, TODAY, start, end)

    for found in plan.items:
        assert found.to_buy_base >= 0
        assert found.available_base >= 0
        assert found.required_base == sum((c.amount_base for c in found.contributions), D(0))
        assert found.to_buy_base == max(D(0), found.required_base - found.available_base)
        assert found.available_base <= found.stock_base
        assert all(start <= c.meal.date <= end for c in found.contributions)
        assert found.contributions or found.unconverted
        if start <= TODAY:
            assert found.reserved_base == 0
    for check in plan.check_have:
        assert all(start <= m.date <= end for m in check.meals)
