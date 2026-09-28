import datetime as dt
from decimal import Decimal

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from jyj.models import (
    Ingredient,
    MealSlot,
    PlannedMeal,
    PlannedMealStatus,
    Recipe,
    RecipeIngredient,
    StockItem,
)
from jyj.services import auth as auth_service
from jyj.shopping.loader import load_and_compute
from jyj.units import UNITS, Dimension, Quantity, UnconvertibleReason

D = Decimal
TODAY = dt.date(2026, 10, 1)
START = dt.date(2026, 10, 5)
END = dt.date(2026, 10, 11)


def test_load_and_compute(db_session: Session) -> None:
    user = auth_service.create_user(db_session, "shopper", "Shopper", "correct horse battery")
    lunch, dinner = db_session.scalars(select(MealSlot).order_by(MealSlot.position)).all()[:2]
    flour = Ingredient(name="Flour", dimension=Dimension.MASS, default_unit="g", category="Baking")
    milk = Ingredient(name="Milk", dimension=Dimension.VOLUME, default_unit="ml", category="Dairy")
    salt = Ingredient(name="Salt", dimension=Dimension.MASS, default_unit="g")
    db_session.add_all([flour, milk, salt])
    db_session.flush()

    def recipe(name: str, *lines: tuple[Ingredient, str | None, str, Dimension]) -> Recipe:
        r = Recipe(name=name, created_by=user.id)
        r.ingredients = [
            RecipeIngredient(
                ingredient_id=i.id,
                amount_per_person=None if a is None else D(a),
                unit_code=u,
                unit_dimension=dim,
                position=pos,
            )
            for pos, (i, a, u, dim) in enumerate(lines)
        ]
        db_session.add(r)
        return r

    pancakes = recipe(
        "Pancakes",
        (flour, "100", "g", Dimension.MASS),
        (milk, "0.2", "l", Dimension.VOLUME),
        (salt, None, "to_taste", Dimension.NONE),
    )
    bread = recipe(
        "Bread", (flour, "0.25", "kg", Dimension.MASS), (milk, "50", "g", Dimension.MASS)
    )
    db_session.flush()

    def plan(day: dt.date, r: Recipe, servings: int, slot: MealSlot = lunch, **kw) -> None:
        db_session.add(
            PlannedMeal(
                date=day,
                slot_id=slot.id,
                recipe_id=r.id,
                servings=servings,
                created_by=user.id,
                **kw,
            )
        )

    plan(TODAY, pancakes, 1)
    plan(START, pancakes, 2)
    plan(START, bread, 2, dinner)
    plan(END, pancakes, 1)
    plan(END + dt.timedelta(days=1), bread, 9)
    plan(TODAY - dt.timedelta(days=1), bread, 9)
    plan(
        START,
        bread,
        9,
        dinner,
        status=PlannedMealStatus.COOKED,
        cooked_at=dt.datetime.now(dt.UTC),
        cooked_by=user.id,
    )
    db_session.add_all(
        [
            StockItem(ingredient_id=flour.id, quantity_base=D("400")),
            StockItem(ingredient_id=milk.id, quantity_base=D("1000")),
        ]
    )
    db_session.flush()
    db_session.expire_all()

    statements: list[str] = []

    def record(conn, cursor, statement, *args) -> None:
        statements.append(statement)

    bind = db_session.connection()
    event.listen(bind, "before_cursor_execute", record)
    try:
        plan_result = load_and_compute(db_session, START, END, TODAY)
    finally:
        event.remove(bind, "before_cursor_execute", record)

    assert len(statements) == 4

    by_name = {i.name: i for i in plan_result.items}
    assert list(by_name) == ["Flour", "Milk"]
    f = by_name["Flour"]
    assert (f.required_base, f.reserved_base, f.available_base, f.to_buy_base) == (
        D("800.000"),
        D("100.000"),
        D("300.000"),
        D("500.000"),
    )
    assert f.display == Quantity(D("500.000"), UNITS["g"])
    assert [(c.meal.date, c.meal.slot_name, c.meal.recipe_name) for c in f.contributions] == [
        (START, lunch.name, "Pancakes"),
        (START, dinner.name, "Bread"),
        (END, lunch.name, "Pancakes"),
    ]
    m = by_name["Milk"]
    assert (m.required_base, m.reserved_base, m.available_base, m.to_buy_base) == (
        D("600.000"),
        D("200.000"),
        D("800.000"),
        D("0.000"),
    )
    assert [(u.amount, u.unit_code, u.reason, u.recipe_names) for u in m.unconverted] == [
        (D("100.000"), "g", UnconvertibleReason.MISSING_GRAMS_PER_ML, ("Bread",))
    ]
    (check,) = plan_result.check_have
    assert (check.name, check.recipe_names, len(check.meals)) == ("Salt", ("Pancakes",), 2)


def test_load_and_compute_with_no_meals(db_session: Session) -> None:
    plan = load_and_compute(db_session, START, END, TODAY)

    assert (plan.items, plan.check_have) == ((), ())
